# esp32-dimmer-pt4112

ESP32-C3 Super Mini + PT4112 LED 드라이버 PWM 디머.
BLE UART(Nordic UART Service)와 USB Serial 양쪽에서 **동일한 텍스트 프로토콜**로 제어한다.
채널 수는 `firmware/esp32-dimmer-pt4112/config.h`의 `CHANNEL_PINS[]`에 핀만 추가하면 늘어난다.

```
firmware/esp32-dimmer-pt4112/   Arduino 스케치 (NimBLE-Arduino 2.x, esp32 core 3.x)
gui/dimmer_gui.py               tkinter 제어 GUI (BLE / USB Serial)
```

## 배선

실제 사용 모듈은 **PT4115** 벅 정전류 드라이버 보드(헤더 VIN/GND/DIM/GND, 터미널 LED+/LED-).

| 채널 | ESP32-C3 | PT4115 모듈 (각 채널마다 모듈 1개) |
|------|----------|------------------------------------|
| CH0  | GPIO0    | DIM                                |
| CH1  | GPIO1    | DIM                                |
| CH2  | GPIO3    | DIM                                |
| CH3  | GPIO4    | DIM                                |
| 공통 | GND      | GND (핀 1 또는 3)                  |

모듈마다 VIN에 12 V, GND는 전원/ESP32와 전부 공통. 채널 수는 `config.h`의 `CHANNEL_PINS[]`로 바꾼다.

- PT4115는 **정전류** 드라이버다. LED+/LED-는 전압 출력이 아니라 센스저항으로 정한 전류(1R0 = 100 mA)를 흘린다.
  저항/드라이버가 내장된 "5 V 모듈" 같은 전압형 부하에는 맞지 않는다. 그런 부하는 로직레벨 N-MOSFET 로우사이드 스위칭으로 PWM 하라.
- VIN은 6 V 이상, 그리고 LED 직렬 전압보다 1 V 이상 높아야 한다. VIN 5 V로 5 V LED는 구동 불가.
- DIM 핀: ≥ 2.5 V 100 %, < 0.3 V 꺼짐(대기), PWM 100 Hz ~ 20 kHz, 내부 풀업이라 핀을 띄우면 100 %.
  기본 설정 1 kHz / 8-bit (DIM high = ON). 부팅 직후 핀을 LOW로 잡아 풀업 점등을 막는다.
- 12 V LED를 쓸 때: 부하가 "12 V 스트립"(저항 내장)이면 드라이버가 전류를 100 mA로 제한하므로 정격보다 어둡게 켜진다.
  더 밝게 하려면 센스저항을 바꿔 전류를 올리고(R330 = 300 mA 등), 부하가 12 V 가까이 먹으면 VIN을 13~15 V로 올려 1 V 이상 헤드룸을 확보한다.

## 빌드 / 업로드

```bash
arduino-cli compile -b esp32:esp32:esp32c3:CDCOnBoot=cdc firmware/esp32-dimmer-pt4112
```

```bash
arduino-cli upload -b esp32:esp32:esp32c3:CDCOnBoot=cdc -p /dev/cu.usbmodem13101 firmware/esp32-dimmer-pt4112
```

`CDCOnBoot=cdc`가 있어야 USB 네이티브 포트로 `Serial`이 나온다.

## GUI

```bash
pip install -r gui/requirements.txt
```

```bash
python gui/dimmer_gui.py
```

**macOS 주의**: BLE는 Python을 실행한 *터미널 앱*이 `NSBluetoothAlwaysUsageDescription`을 선언해야 동작한다.
Terminal.app, iTerm2는 되지만 Warp 등은 없어서 `zsh: abort` 로 즉사한다. `gui/run_gui.command`를
더블클릭하거나 `open -a Terminal gui/run_gui.command` 로 실행하면 된다. 첫 실행 시 Bluetooth 권한 허용.

**Windows**: python.org 배포판(tkinter 포함)에서 `pip install -r gui/requirements.txt` 후 `python gui/dimmer_gui.py`.
BLE는 Windows 10 1709 이상 필요, 별도 권한 없음. ESP32-C3 USB CDC는 Win10+ 기본 드라이버로 `COMx`로 잡힌다.

### Windows 단일 exe 만들기

exe는 Windows에서만 빌드된다(PyInstaller는 크로스 빌드 불가). 두 가지 방법:

1. **Windows PC에서**: python.org 파이썬 설치 후 `gui\build_windows.bat` 더블클릭 → `gui\dist\PT4112Dimmer.exe`.
2. **GitHub Actions**: 저장소에 push하면 `.github/workflows/build-windows.yml`이 `windows-latest`에서 빌드해
   Actions 탭의 Artifacts에 `PT4112Dimmer-windows`로 올려준다. Actions 탭에서 "Run workflow"로 수동 실행도 가능.

빌드 설정은 `gui/PT4112Dimmer.spec`. bleak의 WinRT 백엔드가 여러 `winrt-*` 패키지로 쪼개져 있어서
hidden import로 명시해 두었다. exe가 바로 죽으면 spec의 `console=True`로 바꿔 콘솔에서 traceback 확인.

BLE 모드: Scan → `PT4115-Dimmer` 선택 → Connect.
USB Serial 모드: 포트 선택 → Connect. 연결되면 `INFO`로 채널 수를 받아 슬라이더를 자동 생성한다.

## 프로토콜

BLE UART와 USB Serial은 **완전히 동일한 텍스트 프로토콜**을 쓴다. 전송 계층만 다르고
명령·응답 문법, 상태 push 동작은 같다. 두 링크를 동시에 열어도 되고, 한쪽에서 바꾼 값은
양쪽 모두에 `STATE`로 push된다.

### 공통 규칙

- **줄 단위**. 한 줄 = 명령 하나. `\n` 또는 `\r` 를 만나면 줄이 끝난 것으로 처리한다
  (즉 `\r\n`을 보내도 빈 줄은 무시되므로 명령 하나로 해석된다).
- **명령 이름은 대소문자 무시**. `set`, `SET`, `Set` 모두 동일. 인자는 10진 정수만 허용.
- **구분자는 공백**. 앞뒤 공백/탭/CR은 잘라낸다.
- **한 줄 최대 95 바이트** (`LINE_BUF_SIZE 96`). 넘으면 그 줄은 버려지고 응답도 없다.
- 응답은 항상 한 줄 이상이며, 값이 바뀌는 명령은 `OK` **다음에** `STATE ...`가 따라온다.
- 알 수 없는 명령이나 인자 오류는 `ERR <reason>` 한 줄.

### 명령

| 명령 | 인자 | 응답 | 설명 |
|------|------|------|------|
| `SET <ch> <level> [fade_ms]` | ch: 0 ~ 채널수-1<br>level: 0~255<br>fade_ms: 0 이상 | `OK`<br>`STATE <l0>,<l1>,...` | 채널 하나 밝기 지정. `fade_ms` 생략 시 300 ms, `0`이면 즉시 |
| `ALL <level> [fade_ms]` | level: 0~255<br>fade_ms: 0 이상 | `OK`<br>`STATE <l0>,<l1>,...` | 전 채널 동시 지정 |
| `GET <ch>` | ch | `CH <ch> <level>` | 해당 채널의 **목표값** |
| `STATE` | 없음 | `STATE <l0>,<l1>,...` | 전 채널 목표값, 쉼표 구분 |
| `INFO` | 없음 | `INFO ch=<n> fw=<ver> freq=<hz> max=255` | 채널 수, 펌웨어 버전, PWM 주파수, 최대 level |
| `PING` | 없음 | `PONG` | 연결 확인 |

### 오류 응답

| 응답 | 발생 조건 |
|------|-----------|
| `ERR bad channel` | `SET`/`GET`의 ch가 정수가 아니거나 채널 수 이상 |
| `ERR bad level` | level이 정수가 아니거나 255 초과 |
| `ERR bad fade` | fade_ms가 정수가 아님 |
| `ERR unknown command` | 위 표에 없는 명령 |

음수는 파싱 단계에서 정수로 인정되지 않으므로 해당 인자의 `ERR bad ...`가 된다.

### 상태 push (polling 불필요)

목표값이 바뀌면 — 어느 링크에서 왔든 — 장치가 `STATE <l0>,<l1>,...` 를 **BLE와 Serial 양쪽에 모두**
밀어준다. 그래서 키오스크/상위 앱은 `STATE` 줄만 파싱하면 다른 클라이언트가 바꾼 값까지 항상 동기화된다.

```
← SET 0 200
→ OK
→ STATE 200,0,0,0
```

부팅 직후에도 NVS에서 복원한 값으로 `STATE ...`를 한 번 보낸다.

### 값의 의미

- `level`은 **목표값**이다. 페이드 중이어도 `GET`/`STATE`는 최종 목표를 돌려준다.
  현재 순간 duty를 읽는 명령은 없다.
- 페이드는 5 ms 틱으로 선형 보간한다. `fade_ms`를 5보다 작게 주면 1틱(=5 ms)으로 끝난다.
- 목표값은 2초 debounce 후 NVS에 저장되고 재부팅 시 500 ms 소프트 페이드로 복원된다
  (`SAVE_LEVELS_TO_NVS`, `SAVE_DEBOUNCE_MS`).

---

## USB Serial 링크

| 항목 | 값 |
|------|-----|
| 포트 | ESP32-C3 USB CDC (native USB) |
| 속도 | 115200 8N1 (CDC라 실제로는 무시되지만 열 때 지정해야 함) |
| 장치 → 호스트 줄끝 | `\r\n` (Arduino `println`) |
| 호스트 → 장치 줄끝 | `\n` 또는 `\r\n` 둘 다 가능 |
| 흐름 제어 | 없음 |

- 펌웨어를 `CDCOnBoot=cdc`로 빌드해야 `Serial`이 USB 포트로 나온다. 아니면 하드웨어 UART로 간다.
- macOS: `/dev/cu.usbmodem*`, Linux: `/dev/ttyACM*`, Windows: `COMx` (Win10+ 기본 드라이버).
- **`#`로 시작하는 줄은 디버그 로그이고 Serial 전용이다** (BLE로는 가지 않는다).
  `# PT4115-Dimmer fw=1.0.0 channels=4 ble=PT4115-Dimmer`, `# BLE connected: ...` 같은 줄은
  프로토콜 응답이 아니므로 파서에서 버려야 한다.
- 포트를 열면 보드가 리셋되는 환경(일부 드라이버)에서는 첫 줄로 부팅 로그가 섞여 들어온다.
  연결 후 `INFO`를 보내 응답을 받는 것으로 준비 완료를 판단하면 안전하다.

### 예: pyserial

```python
import serial

s = serial.Serial("/dev/cu.usbmodem13101", 115200, timeout=1)

s.write(b"INFO\n")
print(s.readline())        # b'INFO ch=4 fw=1.0.0 freq=1000 max=255\r\n'

s.write(b"SET 0 128 500\n")
print(s.readline())        # b'OK\r\n'
print(s.readline())        # b'STATE 128,0,0,0\r\n'

s.write(b"ALL 0 0\n")      # 전 채널 즉시 소등
```

```python
# STATE 줄만 추려 상태 동기화
def poll(s):
    line = s.readline().decode(errors="replace").strip()
    if line.startswith("#") or not line:
        return None
    if line.startswith("STATE "):
        return [int(v) for v in line[6:].split(",")]
    return line        # OK / PONG / CH .. / ERR ..
```

---

## BLE 링크

Nordic UART Service(NUS)를 그대로 쓴다. nRF Connect, Bluefruit Connect 같은 범용 NUS 터미널로도
바로 제어된다.

| 항목 | 값 |
|------|-----|
| Advertising 이름 | `PT4115-Dimmer` (`DEVICE_NAME`) |
| Service UUID | `6E400001-B5A3-F393-E0A9-E50E24DCCA9E` |
| RX (클라이언트 → 장치) | `6E400002-B5A3-F393-E0A9-E50E24DCCA9E` — `WRITE`, `WRITE_NO_RESPONSE` |
| TX (장치 → 클라이언트) | `6E400003-B5A3-F393-E0A9-E50E24DCCA9E` — `NOTIFY` |
| 요청 MTU | 128 |
| 장치 → 클라이언트 줄끝 | `\n` (CR 없음) |
| 송신 출력 | `ESP_PWR_LVL_P9` |

### 스캔 / 연결

- Service UUID는 **advertising 패킷**에, 장치 이름은 **scan response**에 실려 있다.
  (legacy adv 31바이트에 128-bit UUID(18) + flags(3)를 넣으면 이름이 안 들어가므로 분리했다.)
  따라서 이름으로 찾으려면 **active scan**을 켜야 한다. passive scan만 하면 UUID로 매칭해야 한다.
- 연결이 끊기면 장치가 스스로 advertising을 재시작한다. 페어링/본딩은 쓰지 않으므로
  재연결에 별도 절차가 없다.
- 동시 연결은 1개 전제로 동작한다 (notify 대상 conn handle 하나를 추적).

### 쓰기 (RX)

- UTF-8/ASCII 텍스트를 그대로 쓴다. 명령 끝에 `\n`을 붙여야 실행된다.
- 한 write에 여러 줄을 담아도 되고, 한 줄을 여러 write로 쪼개 보내도 된다
  (장치 쪽에 줄 조립 버퍼가 있다). 연결이 끊기면 조립 중이던 버퍼는 폐기된다.
- `WRITE_NO_RESPONSE`도 지원하니 빠른 슬라이더 드래그에는 이쪽이 지연이 낮다.
- 한 줄 95 바이트 제한은 Serial과 동일.

### 알림 (TX)

- 응답 한 줄이 notify 1건 이상으로 온다. 줄이 MTU보다 길면 `MTU-3` 바이트 단위로 쪼개 보내므로
  **클라이언트는 notify 조각을 이어붙여 `\n` 기준으로 다시 줄을 잘라야 한다**.
  (현재 명령 응답은 모두 짧아 실제로는 한 조각에 들어간다. 채널 수가 많아지면 `STATE`가 길어진다.)
- 장치는 notify를 ring buffer(16줄)에 담아 메인 루프에서 한 줄씩 내보낸다. 버스트가 몰려
  큐가 꽉 차면 **그 줄은 버린다** — 유실이 의심되면 `STATE`를 다시 질의하면 된다.
- 연결 직후 notify(CCCD)를 반드시 구독해야 응답을 받을 수 있다.

### 예: Python + bleak

```python
import asyncio
from bleak import BleakClient, BleakScanner

NUS_RX = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"   # write
NUS_TX = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"   # notify

async def main():
    dev = await BleakScanner.find_device_by_name("PT4115-Dimmer", timeout=10.0)
    buf = ""

    def on_rx(_, data: bytearray):
        nonlocal buf
        buf += data.decode(errors="replace")
        while "\n" in buf:                      # 조각 재조립
            line, buf = buf.split("\n", 1)
            line = line.strip()
            if line:
                print("<-", line)

    async with BleakClient(dev) as c:
        await c.start_notify(NUS_TX, on_rx)
        await c.write_gatt_char(NUS_RX, b"INFO\n", response=False)
        await c.write_gatt_char(NUS_RX, b"SET 0 255 1000\n", response=False)
        await asyncio.sleep(2)

asyncio.run(main())
```

### 예: nRF Connect 등 범용 NUS 터미널

`PT4115-Dimmer` 연결 → UART 탭 → 전송 문자열 끝에 `LF`(`\n`)를 붙이도록 설정 → `INFO`, `SET 0 128` 입력.

---

## 참고

- 전원 인가 직후 모든 DIM 핀을 LOW로 먼저 잡기 때문에 PT4115 내부 풀업에 의한 full-on 점등이 없다.
- CH0는 보드 내장 LED(GPIO8, active low)에 미러링되어 통신 없이도 동작 확인이 된다.
- `firmware/esp32-dimmer-pt4112/config.h`에서 채널 핀, PWM 주파수/해상도/반전, 기본 페이드,
  NVS 저장 여부를 바꿀 수 있다. 채널 수를 바꾸면 `INFO ch=` 값이 따라 바뀌므로
  상위 앱은 채널 수를 하드코딩하지 말고 `INFO`로 읽는 것이 좋다.
