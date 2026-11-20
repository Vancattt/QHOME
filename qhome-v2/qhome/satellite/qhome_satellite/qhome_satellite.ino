// QHome satellite firmware: turns an Arduino UNO R3, Nano, Mega 2560, UNO R4 Minima or
// UNO R4 WiFi into remote pins for a QHome master (UNO Q or PC).
//
// Upload once. The master decides what every pin does and changes it live; this sketch
// never needs editing again unless you change how the board is connected (config.h).
//
// QLink protocol: short text lines, newline terminated.
//   master -> board   H                      hello            -> h <proto> <model> <boot>
//                     P                      ping             -> p <boot>
//                     C <pin> <mode> <interval_ms> <debounce_ms>  -> c <pin> <mode or error>
//                     D <pin> <0|1>          digital write
//                     A <pin> <0..4095>      PWM / DAC write
//                     R                      release all pins -> r 1
//   board -> master   E <pin> <value>        input changed / periodic reading
//                     B <boot>               the board just started
// Modes: 0 off, 1 input, 2 input pull-up, 3 input pull-down (plain input on AVR),
//        4 output, 5 PWM, 6 analog input, 7 DAC, 8 pulse counter (interrupt pins only)

#include "config.h"

#define QL_PROTO 1
enum { M_OFF = 0, M_DIN = 1, M_DIN_PU = 2, M_DIN_PD = 3, M_DOUT = 4,
       M_PWM = 5, M_ADC = 6, M_DAC = 7, M_CNT = 8 };

// ---------------- board facts ----------------
#if defined(ARDUINO_UNOR4_WIFI)
  #define QL_MODEL "r4w"
  #define QL_R4 1
#elif defined(ARDUINO_UNOR4_MINIMA)
  #define QL_MODEL "r4m"
  #define QL_R4 1
#elif defined(__AVR_ATmega2560__)
  #define QL_MODEL "mega"
#elif defined(NUM_ANALOG_INPUTS) && (NUM_ANALOG_INPUTS > 6)
  #define QL_MODEL "nano"
#else
  #define QL_MODEL "uno"
#endif

#if defined(__AVR_ATmega2560__)
  #define NPIN 70
  #define NCNT 6
#else
  #define NPIN 22
  #define NCNT 2
#endif
#define FIRST_ANALOG A0
#ifndef NOT_AN_INTERRUPT
  #define NOT_AN_INTERRUPT -1
#endif
#if defined(QL_R4)
  #define QL_HAS_PWM(p) ((p) == 3 || (p) == 5 || (p) == 6 || (p) == 9 || (p) == 10 || (p) == 11)
  #define QL_HAS_INT(p) ((p) == 2 || (p) == 3)
#else
  #define QL_HAS_PWM(p) digitalPinHasPWM(p)
  #define QL_HAS_INT(p) (digitalPinToInterrupt(p) != NOT_AN_INTERRUPT)
#endif

#if QL_LINK == QL_UART && defined(QL_R4)
  #define LINK_SERIAL Serial1          // R4: D0/D1 are a separate UART
#else
  #define LINK_SERIAL Serial
#endif
#if QL_LINK == QL_I2C
  #include <Wire.h>
#endif
#if QL_LINK == QL_WIFI
  #include <WiFiS3.h>
#endif

struct PinState {
  uint8_t  mode;
  uint8_t  last;       // last reported level (2 = not reported yet)
  uint8_t  pending;
  uint8_t  slot;       // counter slot
  uint16_t interval;
  uint16_t debounce;
  uint32_t t;          // debounce start or last report time
};
static PinState P[NPIN];
static volatile uint32_t CNT[NCNT];
static int8_t cntPin[NCNT];
static uint16_t bootId;

static void isr0() { CNT[0]++; }
static void isr1() { CNT[1]++; }
#if NCNT > 2
static void isr2() { CNT[2]++; }
static void isr3() { CNT[3]++; }
static void isr4() { CNT[4]++; }
static void isr5() { CNT[5]++; }
static void (*const ISRS[NCNT])() = { isr0, isr1, isr2, isr3, isr4, isr5 };
#else
static void (*const ISRS[NCNT])() = { isr0, isr1 };
#endif

// ---------------- output: one line to the master ----------------
#if QL_LINK == QL_I2C
// The master polls us: it reads 32 bytes at a time out of this ring (zero = nothing).
#define TXN 128
#define RXN 64
static volatile uint8_t txBuf[TXN], rxBuf[RXN];
static volatile uint8_t txHead, txTail, rxHead, rxTail;

static void i2cReceive(int n) {
  while (Wire.available()) {
    uint8_t c = Wire.read(), nx = (rxHead + 1) % RXN;
    if (nx != rxTail) { rxBuf[rxHead] = c; rxHead = nx; }
  }
}
static void i2cRequest() {
  uint8_t out[32];
  for (uint8_t i = 0; i < 32; i++) {
    if (txTail != txHead) { out[i] = txBuf[txTail]; txTail = (txTail + 1) % TXN; }
    else out[i] = 0;
  }
  Wire.write(out, 32);
}
static void outLine(const char *s) {
  noInterrupts();
  uint8_t used = (uint8_t)((txHead + TXN - txTail) % TXN);
  uint8_t need = (uint8_t)strlen(s) + 1;
  if (need < TXN - used) {                       // drop the line if the ring is full
    for (const char *p = s; *p; p++) { txBuf[txHead] = *p; txHead = (txHead + 1) % TXN; }
    txBuf[txHead] = '\n'; txHead = (txHead + 1) % TXN;
  }
  interrupts();
}
#elif QL_LINK == QL_WIFI
static void mqttPublish(const char *topic, const char *payload, bool retain);
static char topicUp[48], topicDown[48], topicStatus[48];
static void outLine(const char *s) { mqttPublish(topicUp, s, false); }
#else
static void outLine(const char *s) { LINK_SERIAL.println(s); }
#endif

static void out2(char k, long a, long b) {
  char buf[32];
  snprintf(buf, sizeof(buf), "%c %ld %ld", k, a, b);
  outLine(buf);
}
static void outBoot(char k) {
  char buf[32];
  if (k == 'h') snprintf(buf, sizeof(buf), "h %d %s %04x", QL_PROTO, QL_MODEL, bootId);
  else snprintf(buf, sizeof(buf), "%c %04x", k, bootId);
  outLine(buf);
}

// ---------------- pins ----------------
static bool linkPin(int p) {
#if QL_LINK == QL_I2C
  return p == SDA || p == SCL;
#elif QL_LINK == QL_WIFI
  return false;
#elif defined(QL_R4)
  return QL_LINK == QL_UART && (p == 0 || p == 1);
#else
  return p == 0 || p == 1;                       // classic boards: USB shares D0/D1
#endif
}

static void releasePin(int p) {
  PinState &s = P[p];
  if (s.mode == M_CNT) { detachInterrupt(digitalPinToInterrupt(p)); cntPin[s.slot] = -1; }
  if (s.mode == M_DOUT || s.mode == M_PWM) digitalWrite(p, LOW);
  if (s.mode != M_OFF && s.mode != M_ADC) pinMode(p, INPUT);
  s.mode = M_OFF;
}

static int configure(int p, int mode, long interval, long debounce) {
  if (p < 0 || p >= NPIN || linkPin(p)) return -1;
  releasePin(p);
  PinState &s = P[p];
  s.interval = interval < 20 ? 20 : (interval > 60000 ? 60000 : (uint16_t)interval);
  s.debounce = debounce < 0 ? 0 : (debounce > 60000 ? 60000 : (uint16_t)debounce);
  s.last = 2; s.pending = 2; s.t = 0;
  switch (mode) {
    case M_OFF: return 0;
    case M_DIN: pinMode(p, INPUT); break;
    case M_DIN_PU: pinMode(p, INPUT_PULLUP); break;
    case M_DIN_PD:
#if defined(INPUT_PULLDOWN)
      pinMode(p, INPUT_PULLDOWN);
#else
      pinMode(p, INPUT); mode = M_DIN;           // AVR has no pull-down: use an external resistor
#endif
      break;
    case M_DOUT: pinMode(p, OUTPUT); digitalWrite(p, LOW); break;
    case M_PWM:
      if (!QL_HAS_PWM(p)) return -2;
      pinMode(p, OUTPUT); analogWrite(p, 0); break;
    case M_ADC:
      if (p < FIRST_ANALOG) return -2;
      break;
    case M_DAC:
#if defined(QL_R4)
      if (p != DAC) return -2;
      analogWrite(DAC, 0); break;
#else
      return -2;
#endif
    case M_CNT: {
      if (!QL_HAS_INT(p)) return -2;
      int slot = -1;
      for (int i = 0; i < NCNT; i++) if (cntPin[i] < 0) { slot = i; break; }
      if (slot < 0) return -3;
      cntPin[slot] = p; s.slot = slot;
      noInterrupts(); CNT[slot] = 0; interrupts();
      pinMode(p, INPUT_PULLUP);
      attachInterrupt(digitalPinToInterrupt(p), ISRS[slot], FALLING);
      break;
    }
    default: return -2;
  }
  s.mode = mode;
  return mode;
}

static void handleLine(char *line) {
  char k = line[0];
  char *q = line + 1;
  long a[4] = { 0, 0, 0, 0 };
  for (int i = 0; i < 4; i++) a[i] = strtol(q, &q, 10);
  switch (k) {
    case 'H': outBoot('h'); break;
    case 'P': outBoot('p'); break;
    case 'C': out2('c', a[0], configure((int)a[0], (int)a[1], a[2], a[3])); break;
    case 'D':
      if (a[0] >= 0 && a[0] < NPIN && P[a[0]].mode == M_DOUT) digitalWrite((int)a[0], a[1] ? HIGH : LOW);
      break;
    case 'A': {
      if (a[0] < 0 || a[0] >= NPIN) break;
      long v = a[1] < 0 ? 0 : (a[1] > 4095 ? 4095 : a[1]);
      uint8_t m = P[a[0]].mode;
#if defined(QL_R4)
      if (m == M_PWM || m == M_DAC) analogWrite((int)a[0], (int)v);       // 12-bit set in setup()
#else
      if (m == M_PWM) analogWrite((int)a[0], (int)(v >> 4));              // 8-bit timers
#endif
      break;
    }
    case 'R':
      for (int p = 0; p < NPIN; p++) releasePin(p);
      outLine("r 1");
      break;
    default: break;                              // unknown or noise: ignore
  }
}

static char inBuf[40];
static uint8_t inLen;
static void feedChar(char c) {
  if (c == '\r') return;
  if (c == '\n') {
    inBuf[inLen] = 0;
    if (inLen > 0) handleLine(inBuf);
    inLen = 0;
    return;
  }
  if (inLen < sizeof(inBuf) - 1) inBuf[inLen++] = c;
  else inLen = 0;                                // too long for us (for example a matrix frame)
}

// ---------------- Wi-Fi + minimal MQTT 3.1.1 client (UNO R4 WiFi) ----------------
#if QL_LINK == QL_WIFI
static WiFiClient net;
static bool mqttUp;
static uint32_t lastPing, lastTry;

static void putStr(uint8_t *b, int &n, const char *s) {
  int l = strlen(s);
  b[n++] = l >> 8; b[n++] = l & 0xFF;
  memcpy(b + n, s, l); n += l;
}
static void sendPacket(uint8_t head, const uint8_t *body, int n) {
  uint8_t h[3]; int hn = 0;
  h[hn++] = head;
  if (n > 127) { h[hn++] = (n & 0x7F) | 0x80; h[hn++] = n >> 7; } else h[hn++] = n;
  net.write(h, hn);
  net.write(body, n);
}
static void mqttPublish(const char *topic, const char *payload, bool retain) {
  if (!mqttUp) return;
  uint8_t b[160]; int n = 0;
  if (strlen(topic) + strlen(payload) + 2 > sizeof(b)) return;
  putStr(b, n, topic);
  int l = strlen(payload);
  memcpy(b + n, payload, l); n += l;
  sendPacket(0x30 | (retain ? 1 : 0), b, n);
}
static bool mqttConnect() {
  if (!net.connect(QL_MASTER_HOST, QL_MASTER_PORT)) return false;
  uint8_t b[200]; int n = 0;
  uint8_t flags = 0x02 | 0x04 | 0x20;            // clean session, will, will retain
  if (strlen(QL_MQTT_USER)) flags |= 0x80;
  if (strlen(QL_MQTT_PASS)) flags |= 0x40;
  putStr(b, n, "MQTT");
  b[n++] = 4; b[n++] = flags; b[n++] = 0; b[n++] = 30;                    // keep-alive 30 s
  putStr(b, n, "qhome-" QL_BOARD_ID);
  putStr(b, n, topicStatus);
  putStr(b, n, "offline");
  if (flags & 0x80) putStr(b, n, QL_MQTT_USER);
  if (flags & 0x40) putStr(b, n, QL_MQTT_PASS);
  sendPacket(0x10, b, n);
  uint32_t t0 = millis();
  while (net.available() < 4 && millis() - t0 < 5000) delay(10);
  if (net.available() < 4) { net.stop(); return false; }
  uint8_t ack[4];
  for (int i = 0; i < 4; i++) ack[i] = net.read();
  if (ack[0] != 0x20 || ack[3] != 0) { net.stop(); return false; }
  n = 0;
  b[n++] = 0; b[n++] = 1;                        // packet id
  putStr(b, n, topicDown);
  b[n++] = 0;                                    // QoS 0
  sendPacket(0x82, b, n);
  mqttUp = true;
  lastPing = millis();
  mqttPublish(topicStatus, "online", true);
  outBoot('B');
  return true;
}
static void mqttLoop() {
  uint32_t now = millis();
  if (WiFi.status() != WL_CONNECTED || !net.connected()) {
    mqttUp = false;
    if (now - lastTry < 3000 && lastTry) return;
    lastTry = now;
    if (WiFi.status() != WL_CONNECTED) {
      WiFi.begin(QL_WIFI_SSID, QL_WIFI_PASS);
      return;
    }
    net.stop();
    mqttConnect();
    return;
  }
  while (net.available() >= 2) {
    uint8_t head = net.read();
    long len = 0; int shift = 0; uint8_t d;
    do { d = net.read(); len |= (long)(d & 0x7F) << shift; shift += 7; } while ((d & 0x80) && shift < 21);
    uint32_t t0 = millis();
    while (net.available() < len && millis() - t0 < 500) delay(1);
    if ((head & 0xF0) == 0x30 && len >= 2) {     // PUBLISH (QoS 0): topic, then our QLink lines
      int tl = (net.read() << 8) | net.read();
      len -= 2;
      while (tl-- > 0 && len-- > 0) net.read();
      while (len-- > 0) feedChar((char)net.read());
      feedChar('\n');
    } else {
      while (len-- > 0) net.read();              // SUBACK, PINGRESP
    }
  }
  if (now - lastPing > 15000) {
    lastPing = now;
    uint8_t none = 0;
    sendPacket(0xC0, &none, 0);
  }
}
#endif

// ---------------- setup / loop ----------------
void setup() {
  for (int i = 0; i < NCNT; i++) cntPin[i] = -1;
  bootId = (uint16_t)(analogRead(A0) * 31 + micros());
#if defined(QL_R4)
  analogReadResolution(14);
  analogWriteResolution(12);
#endif
#if QL_LINK == QL_I2C
  Wire.begin(QL_I2C_ADDRESS);
  #if !QL_I2C_PULLUPS && defined(__AVR__)
  digitalWrite(SDA, LOW);                        // Wire.begin() switched the 5 V pull-ups on
  digitalWrite(SCL, LOW);
  #endif
  Wire.onReceive(i2cReceive);
  Wire.onRequest(i2cRequest);
  outBoot('B');
#elif QL_LINK == QL_WIFI
  snprintf(topicUp, sizeof(topicUp), "qlink/%s/up", QL_BOARD_ID);
  snprintf(topicDown, sizeof(topicDown), "qlink/%s/down", QL_BOARD_ID);
  snprintf(topicStatus, sizeof(topicStatus), "qlink/%s/status", QL_BOARD_ID);
#else
  LINK_SERIAL.begin(QL_BAUD);
  outBoot('B');
#endif
}

void loop() {
#if QL_LINK == QL_I2C
  while (rxTail != rxHead) {
    noInterrupts();
    char c = (char)rxBuf[rxTail]; rxTail = (rxTail + 1) % RXN;
    interrupts();
    feedChar(c);
  }
#elif QL_LINK == QL_WIFI
  mqttLoop();
#else
  while (LINK_SERIAL.available()) feedChar((char)LINK_SERIAL.read());
#endif

  uint32_t now = millis();
  for (int p = 0; p < NPIN; p++) {
    PinState &s = P[p];
    switch (s.mode) {
      case M_DIN: case M_DIN_PU: case M_DIN_PD: {
        uint8_t v = digitalRead(p) ? 1 : 0;
        if (v != s.pending) { s.pending = v; s.t = now; }
        if (s.pending != s.last && (now - s.t) >= s.debounce) {
          s.last = s.pending;
          out2('E', p, s.last);
        }
        break;
      }
      case M_ADC:
        if (now - s.t >= s.interval) { s.t = now; out2('E', p, analogRead(p)); }
        break;
      case M_CNT:
        if (now - s.t >= s.interval) {
          s.t = now;
          noInterrupts(); uint32_t c = CNT[s.slot]; interrupts();
          out2('E', p, (long)c);
        }
        break;
      default: break;
    }
  }
}
