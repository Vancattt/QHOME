// Minimal stand-in for the Arduino core so the sketches can be compiled and exercised on
// a PC (tests/test_firmware.py). stdin carries the serial link; lines starting with '~'
// are test controls:  "~d <pin> <0|1>" input level, "~a <pin> <value>" ADC value,
// "~i <pin> <count>" fire the pin's interrupt.  Output writes are reported on stderr.
#pragma once
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <string>
#include <unistd.h>

typedef uint8_t byte;
#define HIGH 1
#define LOW 0
#define INPUT 0
#define OUTPUT 1
#define INPUT_PULLUP 2
#define FALLING 2
#define NOT_AN_INTERRUPT -1
#if defined(ARDUINO_UNOR4_WIFI) || defined(ARDUINO_UNOR4_MINIMA) || defined(MOCK_UNOQ)
  #define INPUT_PULLDOWN 3
#else
  #define __AVR__ 1
#endif
#if defined(__AVR_ATmega2560__)
  #define A0 54
  #define SDA 20
  #define SCL 21
  #define NUM_ANALOG_INPUTS 16
  #define digitalPinHasPWM(p) (((p) >= 2 && (p) <= 13) || ((p) >= 44 && (p) <= 46))
  #define digitalPinToInterrupt(p) ((p) == 2 ? 0 : ((p) == 3 ? 1 : ((p) >= 18 && (p) <= 21 ? 23 - (p) : NOT_AN_INTERRUPT)))
#else
  #define A0 14
  #define SDA 18
  #define SCL 19
  #ifdef MOCK_NANO
    #define NUM_ANALOG_INPUTS 8
  #else
    #define NUM_ANALOG_INPUTS 6
  #endif
  #define digitalPinHasPWM(p) ((p) == 3 || (p) == 5 || (p) == 6 || (p) == 9 || (p) == 10 || (p) == 11)
  #define digitalPinToInterrupt(p) ((p) == 2 ? 0 : ((p) == 3 ? 1 : NOT_AN_INTERRUPT))
#endif
#define A1 (A0 + 1)
#define A2 (A0 + 2)
#define A3 (A0 + 3)
#define A4 (A0 + 4)
#define A5 (A0 + 5)
#define DAC A0
#define MOCK_NPIN 80

inline int mockLevel[MOCK_NPIN], mockAnalog[MOCK_NPIN];
inline void (*mockIsr[8])();
inline std::string mockRx;          // link bytes waiting for the sketch

inline uint32_t millis() {
  using namespace std::chrono;
  static auto t0 = steady_clock::now();
  return (uint32_t)duration_cast<milliseconds>(steady_clock::now() - t0).count();
}
inline uint32_t micros() { return millis() * 1000u; }
inline void delay(uint32_t ms) { usleep(ms * 1000); }
inline void pinMode(int p, int m) { fprintf(stderr, "PM %d %d\n", p, m); if (m == INPUT_PULLUP && p < MOCK_NPIN && mockLevel[p] < 0) mockLevel[p] = 1; }
inline void digitalWrite(int p, int v) { fprintf(stderr, "DW %d %d\n", p, v); }
inline int digitalRead(int p) { return (p >= 0 && p < MOCK_NPIN && mockLevel[p] > 0) ? 1 : 0; }
inline int analogRead(int p) { return (p >= 0 && p < MOCK_NPIN) ? mockAnalog[p] : 0; }
inline void analogWrite(int p, int v) { fprintf(stderr, "AW %d %d\n", p, v); }
inline void analogReadResolution(int) {}
inline void analogWriteResolution(int) {}
inline void attachInterrupt(int n, void (*fn)(), int) { if (n >= 0 && n < 8) mockIsr[n] = fn; }
inline void detachInterrupt(int n) { if (n >= 0 && n < 8) mockIsr[n] = nullptr; }
inline void noInterrupts() {}
inline void interrupts() {}

// Read stdin without blocking; split test controls from link bytes.
inline void mockPump() {
  static std::string line;
  static bool init = false;
  if (!init) { init = true; fcntl(0, F_SETFL, fcntl(0, F_GETFL) | O_NONBLOCK); for (int i = 0; i < MOCK_NPIN; i++) mockLevel[i] = -1; }
  char buf[256];
  ssize_t n;
  while ((n = read(0, buf, sizeof buf)) > 0) {
    for (ssize_t i = 0; i < n; i++) {
      line += buf[i];
      if (buf[i] != '\n') continue;
      if (line[0] == '~') {
        char k = line[1]; int p = 0; long v = 0;
        sscanf(line.c_str() + 2, "%d %ld", &p, &v);
        if (k == 'd' && p < MOCK_NPIN) mockLevel[p] = (int)v;
        if (k == 'a' && p < MOCK_NPIN) mockAnalog[p] = (int)v;
        if (k == 'i') { int irq = digitalPinToInterrupt(p); for (long c = 0; c < v; c++) if (irq >= 0 && mockIsr[irq]) mockIsr[irq](); }
        if (k == 'q') exit(0);
      } else mockRx += line;
      line.clear();
    }
  }
  if (n == 0) exit(0);               // stdin closed: the test is over
}

struct MockSerial {
  void begin(long) {}
  void end() {}
  int available() { mockPump(); return (int)mockRx.size(); }
  int read() { if (mockRx.empty()) return -1; char c = mockRx[0]; mockRx.erase(0, 1); return (uint8_t)c; }
  void println(const char *s) { printf("%s\n", s); fflush(stdout); }
  void print(const char *s) { printf("%s", s); fflush(stdout); }
};
inline MockSerial Serial, Serial1;

void setup();
void loop();
void mockTick();
