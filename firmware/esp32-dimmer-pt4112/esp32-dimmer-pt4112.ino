// ESP32-C3 + PT4112 multi-channel PWM dimmer
// Control links: BLE UART (Nordic UART Service) and USB Serial, same text protocol.
//
// Protocol (line based, '\n' terminated, case-insensitive commands):
//   SET <ch> <level> [fade_ms]   -> OK            level 0~255
//   ALL <level> [fade_ms]        -> OK
//   GET <ch>                     -> CH <ch> <level>
//   STATE                        -> STATE <l0>,<l1>,...
//   INFO                         -> INFO ch=<n> fw=<ver> freq=<hz> max=<pwm_max>
//   PING                         -> PONG
//   error                        -> ERR <reason>
// Device pushes "STATE ..." to every link whenever a level target changes.

#include <Arduino.h>
#include <NimBLEDevice.h>
#include <Preferences.h>
#include "config.h"

// Nordic UART Service UUIDs
static const char* NUS_SERVICE_UUID = "6E400001-B5A3-F393-E0A9-E50E24DCCA9E";
static const char* NUS_RX_UUID      = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"; // client -> device (write)
static const char* NUS_TX_UUID      = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"; // device -> client (notify)

// ------------------------------------------------------------------ Channel state
struct Channel {
  uint8_t  pin;
  uint8_t  target;     // requested level 0~255
  float    current;    // fading value
  float    step;       // change per tick (signed)
};

static Channel ch[NUM_CHANNELS];
static Preferences prefs;
static bool     levelsDirty   = false;
static uint32_t levelsDirtyAt = 0;

static NimBLECharacteristic* txChar      = nullptr;
static volatile bool         bleConnected = false;

// ------------------------------------------------------------------ Output
static void writePwm(uint8_t idx, uint8_t level) {
  uint32_t duty = level;
#if PWM_RESOLUTION != 8
  duty = (uint32_t)level * PWM_MAX / 255;
#endif
#if PWM_INVERT
  duty = PWM_MAX - duty;
#endif
  ledcWrite(ch[idx].pin, duty);

#if ONBOARD_LED_PIN >= 0
  if (idx == 0) {
    uint32_t ledDuty = (uint32_t)level * PWM_MAX / 255;
#if ONBOARD_LED_ACTIVE_LOW
    ledDuty = PWM_MAX - ledDuty;
#endif
    ledcWrite(ONBOARD_LED_PIN, ledDuty);
  }
#endif
}

static void setLevel(uint8_t idx, uint8_t level, uint32_t fadeMs) {
  Channel& c = ch[idx];
  c.target = level;
  if (fadeMs == 0) {
    c.current = level;
    c.step = 0;
    writePwm(idx, level);
  } else {
    float ticks = (float)fadeMs / FADE_TICK_MS;
    if (ticks < 1) ticks = 1;
    c.step = ((float)level - c.current) / ticks;
  }
  levelsDirty = true;
  levelsDirtyAt = millis();
}

static void fadeTick() {
  static uint32_t last = 0;
  uint32_t now = millis();
  if (now - last < FADE_TICK_MS) return;
  last = now;

  for (uint8_t i = 0; i < NUM_CHANNELS; i++) {
    Channel& c = ch[i];
    if (c.step == 0) continue;
    c.current += c.step;
    bool done = (c.step > 0 && c.current >= c.target) || (c.step < 0 && c.current <= c.target);
    if (done) {
      c.current = c.target;
      c.step = 0;
    }
    writePwm(i, (uint8_t)(c.current + 0.5f));
  }
}

// ------------------------------------------------------------------ Persistence
static void loadLevels() {
#if SAVE_LEVELS_TO_NVS
  prefs.begin("dimmer", true);
  for (uint8_t i = 0; i < NUM_CHANNELS; i++) {
    char key[8];
    snprintf(key, sizeof(key), "l%u", i);
    ch[i].target = prefs.getUChar(key, 0);
  }
  prefs.end();
#endif
}

static void saveLevelsIfNeeded() {
#if SAVE_LEVELS_TO_NVS
  if (!levelsDirty || millis() - levelsDirtyAt < SAVE_DEBOUNCE_MS) return;
  levelsDirty = false;
  prefs.begin("dimmer", false);
  for (uint8_t i = 0; i < NUM_CHANNELS; i++) {
    char key[8];
    snprintf(key, sizeof(key), "l%u", i);
    if (prefs.getUChar(key, 0) != ch[i].target) prefs.putUChar(key, ch[i].target);
  }
  prefs.end();
#else
  levelsDirty = false;
#endif
}

// ------------------------------------------------------------------ Links (output)
// BLE TX goes through a small SPSC ring: commands arrive in the NimBLE host task
// (onWrite) and notifying from inside that callback loses all but the last
// notification of a burst. loop() drains the ring one line per pass instead.
#define BLE_TXQ_SIZE 16
static char              bleTxQ[BLE_TXQ_SIZE][LINE_BUF_SIZE];
static volatile uint8_t  bleTxHead = 0, bleTxTail = 0;   // head: producer, tail: consumer
static volatile uint16_t bleConnHandle = BLE_HS_CONN_HANDLE_NONE;

static void sendLine(const char* line) {
  Serial.println(line);
  if (!bleConnected) return;
  uint8_t next = (bleTxHead + 1) % BLE_TXQ_SIZE;
  if (next == bleTxTail) return;              // queue full: drop (client can re-query STATE)
  snprintf(bleTxQ[bleTxHead], LINE_BUF_SIZE, "%s\n", line);
  bleTxHead = next;
}

static void bleTxPump() {
  if (bleTxTail == bleTxHead) return;
  if (!bleConnected || !txChar) { bleTxTail = bleTxHead; return; }

  const char* s = bleTxQ[bleTxTail];
  size_t len = strlen(s);
  uint16_t mtu = NimBLEDevice::getServer()->getPeerMTU(bleConnHandle);
  size_t chunk = (mtu > 23) ? mtu - 3 : 20;
  for (size_t off = 0; off < len; off += chunk) {
    size_t n = min(chunk, len - off);
    if (!txChar->notify((const uint8_t*)s + off, n, bleConnHandle)) break;
    if (off + n < len) delay(5);
  }
  bleTxTail = (bleTxTail + 1) % BLE_TXQ_SIZE;
}

static void sendState() {
  char buf[8 + NUM_CHANNELS * 4];
  int n = snprintf(buf, sizeof(buf), "STATE ");
  for (uint8_t i = 0; i < NUM_CHANNELS; i++) {
    n += snprintf(buf + n, sizeof(buf) - n, "%s%u", i ? "," : "", ch[i].target);
  }
  sendLine(buf);
}

// ------------------------------------------------------------------ Command parser
static bool parseUInt(const char* s, long& out) {
  if (!s || !*s) return false;
  char* end;
  out = strtol(s, &end, 10);
  return *end == '\0' && out >= 0;
}

static void handleCommand(char* line) {
  // trim
  while (*line == ' ' || *line == '\r' || *line == '\t') line++;
  size_t len = strlen(line);
  while (len && (line[len - 1] == ' ' || line[len - 1] == '\r' || line[len - 1] == '\t')) line[--len] = '\0';
  if (!len) return;

  char* cmd  = strtok(line, " ");
  char* arg1 = strtok(nullptr, " ");
  char* arg2 = strtok(nullptr, " ");
  char* arg3 = strtok(nullptr, " ");
  for (char* p = cmd; *p; p++) *p = toupper(*p);

  long a, b, c;
  char buf[64];

  if (!strcmp(cmd, "PING")) {
    sendLine("PONG");
  } else if (!strcmp(cmd, "INFO")) {
    snprintf(buf, sizeof(buf), "INFO ch=%u fw=%s freq=%u max=255", (unsigned)NUM_CHANNELS, FW_VERSION, PWM_FREQUENCY);
    sendLine(buf);
  } else if (!strcmp(cmd, "STATE")) {
    sendState();
  } else if (!strcmp(cmd, "GET")) {
    if (!parseUInt(arg1, a) || a >= NUM_CHANNELS) { sendLine("ERR bad channel"); return; }
    snprintf(buf, sizeof(buf), "CH %ld %u", a, ch[a].target);
    sendLine(buf);
  } else if (!strcmp(cmd, "SET")) {
    if (!parseUInt(arg1, a) || a >= NUM_CHANNELS) { sendLine("ERR bad channel"); return; }
    if (!parseUInt(arg2, b) || b > 255)           { sendLine("ERR bad level"); return; }
    c = DEFAULT_FADE_MS;
    if (arg3 && !parseUInt(arg3, c))              { sendLine("ERR bad fade"); return; }
    setLevel(a, b, c);
    sendLine("OK");
    sendState();
  } else if (!strcmp(cmd, "ALL")) {
    if (!parseUInt(arg1, a) || a > 255) { sendLine("ERR bad level"); return; }
    c = DEFAULT_FADE_MS;
    if (arg2 && !parseUInt(arg2, c))    { sendLine("ERR bad fade"); return; }
    for (uint8_t i = 0; i < NUM_CHANNELS; i++) setLevel(i, a, c);
    sendLine("OK");
    sendState();
  } else {
    sendLine("ERR unknown command");
  }
}

// Line accumulator shared by both links (each link has its own buffer)
struct LineBuf {
  char   buf[LINE_BUF_SIZE];
  size_t len = 0;
  void feed(const uint8_t* data, size_t n) {
    for (size_t i = 0; i < n; i++) {
      char c = (char)data[i];
      if (c == '\n' || c == '\r') {
        if (len) { buf[len] = '\0'; handleCommand(buf); len = 0; }
      } else if (len < LINE_BUF_SIZE - 1) {
        buf[len++] = c;
      } else {
        len = 0; // overflow: drop line
      }
    }
  }
};
static LineBuf serialLine, bleLine;

// ------------------------------------------------------------------ BLE callbacks
class ServerCb : public NimBLEServerCallbacks {
  void onConnect(NimBLEServer* s, NimBLEConnInfo& info) override {
    bleConnHandle = info.getConnHandle();
    bleConnected = true;
    Serial.printf("# BLE connected: %s\n", info.getAddress().toString().c_str());
  }
  void onDisconnect(NimBLEServer* s, NimBLEConnInfo& info, int reason) override {
    bleConnected = false;
    bleConnHandle = BLE_HS_CONN_HANDLE_NONE;
    bleLine.len = 0;
    Serial.printf("# BLE disconnected (reason %d), advertising\n", reason);
    NimBLEDevice::startAdvertising();
  }
};

class RxCb : public NimBLECharacteristicCallbacks {
  void onWrite(NimBLECharacteristic* c, NimBLEConnInfo& info) override {
    NimBLEAttValue v = c->getValue();
    bleLine.feed(v.data(), v.length());
  }
};

static ServerCb serverCb;
static RxCb     rxCb;

static void setupBle() {
  NimBLEDevice::init(DEVICE_NAME);
  NimBLEDevice::setPower(ESP_PWR_LVL_P9);
  NimBLEDevice::setMTU(128);

  NimBLEServer* server = NimBLEDevice::createServer();
  server->setCallbacks(&serverCb);

  NimBLEService* svc = server->createService(NUS_SERVICE_UUID);
  NimBLECharacteristic* rx = svc->createCharacteristic(
      NUS_RX_UUID, NIMBLE_PROPERTY::WRITE | NIMBLE_PROPERTY::WRITE_NR);
  rx->setCallbacks(&rxCb);
  txChar = svc->createCharacteristic(NUS_TX_UUID, NIMBLE_PROPERTY::NOTIFY);
  // NimBLE 2.x: services start automatically when advertising begins

  // Legacy adv payload is 31 bytes: flags(3) + 128-bit UUID(18) leaves no room
  // for the name, so enable scan response FIRST; setName() then puts the name there.
  NimBLEAdvertising* adv = NimBLEDevice::getAdvertising();
  adv->enableScanResponse(true);
  bool okName = adv->setName(DEVICE_NAME);
  bool okUuid = adv->addServiceUUID(NUS_SERVICE_UUID);
  bool okAdv  = adv->start();
  Serial.printf("# BLE adv name=%d uuid=%d start=%d\n", okName, okUuid, okAdv);
}

// ------------------------------------------------------------------ Setup / loop
void setup() {
  // PT4115 DIM has an internal pull-up: a floating GPIO means 100% brightness.
  // Drive every channel pin LOW before anything else (Serial init, BLE, NVS).
  for (uint8_t i = 0; i < NUM_CHANNELS; i++) {
    pinMode(CHANNEL_PINS[i], OUTPUT);
    digitalWrite(CHANNEL_PINS[i], PWM_INVERT ? HIGH : LOW);
  }
#if ONBOARD_LED_PIN >= 0
  pinMode(ONBOARD_LED_PIN, OUTPUT);
  digitalWrite(ONBOARD_LED_PIN, ONBOARD_LED_ACTIVE_LOW ? HIGH : LOW);
#endif

  Serial.begin(SERIAL_BAUD);
  delay(300);

  for (uint8_t i = 0; i < NUM_CHANNELS; i++) {
    ch[i].pin = CHANNEL_PINS[i];
    ch[i].target = 0;
    ch[i].current = 0;
    ch[i].step = 0;
    ledcAttach(ch[i].pin, PWM_FREQUENCY, PWM_RESOLUTION);
    writePwm(i, 0);
  }
#if ONBOARD_LED_PIN >= 0
  ledcAttach(ONBOARD_LED_PIN, PWM_FREQUENCY, PWM_RESOLUTION);
#endif

  loadLevels();
  for (uint8_t i = 0; i < NUM_CHANNELS; i++) {
    uint8_t restored = ch[i].target;
    ch[i].current = 0;
    writePwm(i, 0);
    setLevel(i, restored, 500);   // soft power-on to last level
  }
  levelsDirty = false;

  setupBle();

  Serial.printf("# %s fw=%s channels=%u ble=%s\n", DEVICE_NAME, FW_VERSION, (unsigned)NUM_CHANNELS, DEVICE_NAME);
  sendState();
}

void loop() {
  while (Serial.available()) {
    uint8_t b = (uint8_t)Serial.read();
    serialLine.feed(&b, 1);
  }
  bleTxPump();
  fadeTick();
  saveLevelsIfNeeded();
}
