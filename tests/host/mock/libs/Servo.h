// Stand-in for the Servo library (AVR, UNO R4 and ESP8266 cores). Reports on stderr:
// SA <pin> attach, SW <pin> <degrees> write, SD <pin> detach.
#pragma once
#include "Arduino.h"
class Servo {
 public:
  uint8_t attach(int p) { mockBad("servo", p); pin_ = p; fprintf(stderr, "SA %d\n", p); return 0; }
  void write(int v) { if (pin_ >= 0) fprintf(stderr, "SW %d %d\n", pin_, v); }
  void detach() { if (pin_ >= 0) fprintf(stderr, "SD %d\n", pin_); pin_ = -1; }
  bool attached() { return pin_ >= 0; }
 private:
  int pin_ = -1;
};
