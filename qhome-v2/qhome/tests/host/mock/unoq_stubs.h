// Compile-only stand-ins for the UNO Q libraries used by sketch/sketch.ino.
#pragma once
#define MOCK_UNOQ 1
#include "Arduino.h"
struct String {
  std::string s;
  String() {}
  String(const char *c) : s(c) {}
  int length() const { return (int)s.size(); }
  char charAt(int i) const { return s[i]; }
};
struct TwoWire {
  void begin() {}
  void beginTransmission(uint8_t) {}
  size_t write(uint8_t) { return 1; }
  int endTransmission() { return 0; }
  int requestFrom(int, int) { return 0; }
  int available() { return 0; }
  int read() { return -1; }
};
inline TwoWire Wire, Wire1;
struct UartStub { void begin(long) {} void end() {} int available() { return 0; } int read() { return -1; } void print(const String &) {} };
#define Serial1 Serial1Stub
inline UartStub Serial1Stub;
struct BridgeStub {
  void begin() {}
  template <typename F> bool provide_safe(const char *, F) { return true; }
  template <typename... A> void notify(const char *, A...) {}
};
inline BridgeStub Bridge;
struct Arduino_LED_Matrix { void begin() {} void setGrayscaleBits(int) {} void draw(uint8_t *) {} };
enum { LED3_R = 50, LED3_G, LED3_B, LED4_R, LED4_G, LED4_B };
#define DAC0 14
#define DAC1 15
