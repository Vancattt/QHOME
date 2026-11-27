// Stand-in for the SPI library. Reports SPI 1 on begin() and SPI 0 on end().
#pragma once
#include "Arduino.h"
struct SPIClass {
  void begin() { fprintf(stderr, "SPI 1\n"); }
  void end() { fprintf(stderr, "SPI 0\n"); }
};
inline SPIClass SPI;
