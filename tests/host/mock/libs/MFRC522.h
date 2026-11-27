// Stand-in for the MFRC522 library (RC522 NFC reader, v1.4). A card is put in front of the
// reader with the test control "~n <hex id>"; it is read once, then sleeps (HaltA) as a real card does.
// Reports NI <cs pin> <rst pin> on PCD_Init() and NH on PICC_HaltA().
#pragma once
#include "Arduino.h"
class MFRC522 {
 public:
  static constexpr uint8_t UNUSED_PIN = 255;
  struct Uid { byte size; byte uidByte[10]; byte sak; } uid;
  void PCD_Init(byte cs, byte rst) { mockBad("rc522", cs); fprintf(stderr, "NI %d %d\n", cs, rst); }
  void PCD_AntennaOff() { fprintf(stderr, "NO\n"); }
  bool PICC_IsNewCardPresent() { mockPump(); return !mockCard.empty(); }
  bool PICC_ReadCardSerial() {
    if (mockCard.empty()) return false;
    uid.size = 0;
    for (size_t i = 0; i + 1 < mockCard.size() && uid.size < 10; i += 2)
      uid.uidByte[uid.size++] = (byte)strtol(mockCard.substr(i, 2).c_str(), nullptr, 16);
    return true;
  }
  void PICC_HaltA() { mockCard.clear(); fprintf(stderr, "NH\n"); }
};
