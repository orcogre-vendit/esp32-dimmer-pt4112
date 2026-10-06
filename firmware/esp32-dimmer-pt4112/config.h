#pragma once

// ===== Device =====
#define DEVICE_NAME       "PT4115-Dimmer"   // BLE advertising name (GUI matches "dimmer" or NUS UUID)
#define FW_VERSION        "1.0.0"

// ===== Channels =====
// 각 PT4115 모듈 DIM 핀에 연결된 GPIO 목록. 배열 인덱스 = 채널 번호.
// ESP32-C3 스트래핑 핀(GPIO2, 8, 9)과 USB(18/19), UART(20/21)는 피할 것.
// 남는 안전한 핀: 0, 1, 3, 4, 5, 6, 7, 10
static const uint8_t CHANNEL_PINS[] = {0, 1, 3, 4};   // CH0=GPIO0, CH1=GPIO1, CH2=GPIO3, CH3=GPIO4
#define NUM_CHANNELS      (sizeof(CHANNEL_PINS) / sizeof(CHANNEL_PINS[0]))

// ===== PWM =====
// 실제 모듈은 PT4115 (벅 정전류 드라이버, VIN 6~30V, 출력은 VIN보다 1V 이상 낮아야 함).
// DIM 핀: >=2.5V 100%, <0.3V 꺼짐(대기), PWM 100Hz~20kHz, 내부 풀업(플로팅 = 100%).
// 1kHz / 8-bit: LSB 3.9us, 플리커 없고 PT4115 범위 안. 부팅 시 핀을 먼저 LOW로 잡아 풀업 점등 방지.
#define PWM_FREQUENCY     1000  // Hz
#define PWM_RESOLUTION    8     // bit -> level 0~255
#define PWM_INVERT        0     // DIM high = ON. 1이면 duty 반전
#define PWM_MAX           ((1 << PWM_RESOLUTION) - 1)

// ===== Fade =====
#define DEFAULT_FADE_MS   300   // SET 명령의 기본 페이드 시간 (0 = 즉시)
#define FADE_TICK_MS      5     // 페이드 갱신 주기

// ===== Onboard LED (debug mirror of channel 0) =====
#define ONBOARD_LED_PIN   8     // ESP32-C3 Super Mini 내장 LED (active LOW). -1이면 비활성
#define ONBOARD_LED_ACTIVE_LOW 1

// ===== Persistence =====
#define SAVE_LEVELS_TO_NVS 1    // 마지막 밝기를 NVS에 저장하고 부팅 시 복원
#define SAVE_DEBOUNCE_MS   2000

// ===== Serial =====
#define SERIAL_BAUD       115200
#define LINE_BUF_SIZE     96
