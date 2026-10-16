/*
 * Arduino Uno as an IoT-ITS device behind a serial link.
 *
 * The Uno has no network interface, so it cannot speak MQTT. It describes
 * itself over USB serial and a host (devices/serial_agent.py on the Pi 5)
 * announces it to the ITS network on its behalf.
 *
 * Serial protocol (115200 baud, one JSON object per line):
 *   {"type":"announce", ...}   once at boot, and again when the host sends '?'
 *   {"type":"hb","uptime_ms":N,"memory_free":N}   every HEARTBEAT_MS
 *
 * The announcement carries what the board itself can report: CPU clock,
 * SRAM size and free SRAM, flash size. The device id is assigned by the host
 * (the Uno has no factory MAC or unique id readable from the sketch).
 *
 * Opening the serial port from the host resets the Uno (DTR auto-reset), so
 * every time the agent opens the port the board boots and announces again.
 *
 * Project: PINV01-24 - FIUNA
 */

const unsigned long BAUD = 115200;
const unsigned long HEARTBEAT_MS = 5000;
const char FIRMWARE_VERSION[] = "uno-serial-1.0";

unsigned long tSetup = 0;      // millis() on entering setup()
unsigned long lastHb = 0;

// Free SRAM between the heap and the stack (standard AVR idiom).
extern int __heap_start, *__brkval;
int freeRam() {
  int v;
  return (int)&v - (__brkval == 0 ? (int)&__heap_start : (int)__brkval);
}

void sendAnnounce() {
  // Built with plain prints: no String or JSON library, to keep SRAM free.
  Serial.print(F("{\"type\":\"announce\",\"hardware\":\"Arduino Uno R3 (ATmega328P)\""));
  Serial.print(F(",\"mcu\":\"ATmega328P\",\"cpu_freq_mhz\":"));
  Serial.print(F_CPU / 1000000UL);
  Serial.print(F(",\"memory_total\":2048,\"memory_free\":"));
  Serial.print(freeRam());
  Serial.print(F(",\"flash_total\":32768,\"firmware_version\":\""));
  Serial.print(FIRMWARE_VERSION);
  Serial.print(F("\",\"setup_ms\":"));
  Serial.print(tSetup);
  Serial.print(F(",\"announce_ms\":"));
  Serial.print(millis());
  Serial.println(F("}"));
}

void sendHeartbeat() {
  Serial.print(F("{\"type\":\"hb\",\"uptime_ms\":"));
  Serial.print(millis());
  Serial.print(F(",\"memory_free\":"));
  Serial.print(freeRam());
  Serial.println(F("}"));
}

void setup() {
  tSetup = millis();
  Serial.begin(BAUD);
  pinMode(LED_BUILTIN, OUTPUT);
  sendAnnounce();
  lastHb = millis();
}

void loop() {
  // The host asks for the announcement again with '?'
  while (Serial.available() > 0) {
    if (Serial.read() == '?') {
      sendAnnounce();
    }
  }

  if (millis() - lastHb >= HEARTBEAT_MS) {
    lastHb = millis();
    sendHeartbeat();
    digitalWrite(LED_BUILTIN, !digitalRead(LED_BUILTIN));  // visible sign of life
  }
}
