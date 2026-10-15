/*
 * mqtt_device_olimex_poe.ino
 * Olimex ESP32-PoE (LAN8720, RMII) -> MQTT over Ethernet, IoT-ITS announcement format (PINV01-24)
 *
 * Arduino IDE:
 *   Board:  "OLIMEX ESP32-PoE"  (esp32 core 3.x)
 *   Libs:   PubSubClient 2.8, ArduinoJson 7.x
 *
 * IMPORTANT: do not use USB and PoE at the same time (the board is not isolated).
 *
 * Onboarding-latency instrumentation
 * ----------------------------------
 * Once it has an IP address, the board sets its clock from NTP_SERVER (the
 * Raspberry Pi 5, whose clock is disciplined by the GPS PPS signal) with a
 * minimal NTP client: NTP_SAMPLES requests, and the clock is set from the one
 * with the shortest round trip (server transmit time + half the round trip).
 * The ESP-IDF SNTP client is not used: it does not compensate the round trip
 * and waits a random time before its first request. Every registration
 * carries a "timing" block with the millis() value of each boot stage (setup,
 * serial wait, link with IP, clock synchronized, MQTT connected, publish), the
 * round trip of the NTP sample used and the wall-clock time of the publication. The
 * MQTT->HTTP bridge removes the block before forwarding the announcement, so
 * the server sees the usual registration. The "time_probe" command answers
 * with the board's clock at reception and at reply (experiments/clock_probe.py
 * estimates the clock offset). The NTP step exists only for the measurement
 * and is reported as a separate stage.
 */

#include <ETH.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <time.h>
#include <sys/time.h>

// ---------- Configuration ----------
const char* MQTT_BROKER = "192.168.0.21";   // IP of the Mosquitto broker
const uint16_t MQTT_PORT = 1883;
const uint16_t MQTT_KEEPALIVE_SECONDS = 15;

// Time server for the measurement: the Pi 5 (chrony must "allow" the LAN)
const char* NTP_SERVER = "192.168.0.21";
const uint16_t NTP_LOCAL_PORT = 8123;
const int NTP_SAMPLES = 4;                  // requests; the shortest round trip wins
const unsigned long NTP_REPLY_TIMEOUT_MS = 500;
const unsigned long NTP_SAMPLE_GAP_MS = 50;

const char* DEVICE_ID     = "esp32-olimex-poe-01";
const char* HARDWARE_TYPE = "ESP32-PoE-Olimex";   // contains 'esp32'
const char* LOCATION      = "Lab-Test";
const char* LINK_TYPE     = "ethernet";
const char* TOPIC_PREFIX  = "its";

const unsigned long HEARTBEAT_MS = 30000;
const unsigned long DATA_MS      = 60000;

// Ethernet pins of the Olimex ESP32-PoE (LAN8720)
#define ETH_PHY_TYPE_OLX  ETH_PHY_LAN8720
#define ETH_PHY_ADDR_OLX  0
#define ETH_PHY_MDC_OLX   23
#define ETH_PHY_MDIO_OLX  18
#define ETH_PHY_POWER_OLX 12
#define ETH_CLK_MODE_OLX  ETH_CLOCK_GPIO17_OUT

// ---------- State ----------
WiFiClient netClient;
PubSubClient mqtt(netClient);
WiFiUDP ntpUdp;                       // UDP over the Ethernet interface (shared lwIP stack)

static bool ethGotIP = false;
bool registered = false;
unsigned long lastHeartbeat = 0, lastData = 0, lastReconnect = 0;

String topicRegister, topicState, topicHeartbeat, topicData, topicCommand, topicResponse;

// Onboarding marks (millis() at each boot stage; 0 = not reached yet)
unsigned long tSetup = 0, tSerial = 0, tSync = 0, tMqtt = 0;
volatile unsigned long tLink = 0;     // written by the network event handler
bool timeSynced = false;
bool clockTried = false;
uint32_t syncRttUs = 0;               // round trip of the NTP sample that set the clock
uint32_t registrationSeq = 0;         // 1 = first registration since boot
int64_t lastRecvEpochMs = 0;          // clock when the last MQTT message arrived

// ---------- Clock ----------

// Wall-clock time in milliseconds since the epoch (0 before SNTP sync).
int64_t epochMs() {
  struct timeval tv;
  gettimeofday(&tv, NULL);
  return (int64_t)tv.tv_sec * 1000LL + (int64_t)(tv.tv_usec / 1000);
}

// 64-bit values travel as strings: the JSON library may not keep all the
// digits of a 13-digit millisecond timestamp as a number.
String epochString(int64_t value) {
  char buf[24];
  snprintf(buf, sizeof(buf), "%lld", (long long)value);
  return String(buf);
}

static uint32_t be32(const uint8_t* b) {
  return ((uint32_t)b[0] << 24) | ((uint32_t)b[1] << 16) | ((uint32_t)b[2] << 8) | (uint32_t)b[3];
}

// Minimal NTP client: NTP_SAMPLES requests; the clock is set from the reply
// with the shortest round trip, as the server's transmit time plus half that
// round trip, advanced by the time elapsed since the reply arrived.
bool ntpSync() {
  IPAddress server;
  if (!server.fromString(NTP_SERVER) || !ntpUdp.begin(NTP_LOCAL_PORT)) {
    return false;
  }
  bool have = false;
  uint32_t bestRttUs = 0xFFFFFFFFUL;
  uint32_t bestRecvUs = 0;
  int64_t bestEpochUs = 0;
  for (int i = 0; i < NTP_SAMPLES; i++) {
    while (ntpUdp.parsePacket() > 0) {
      ntpUdp.flush();                 // drop stale replies
    }
    uint8_t request[48] = {0};
    request[0] = 0x23;                // LI = 0, version 4, mode 3 (client)
    uint32_t sentUs = micros();
    ntpUdp.beginPacket(server, 123);
    ntpUdp.write(request, sizeof(request));
    ntpUdp.endPacket();

    unsigned long start = millis();
    while (millis() - start < NTP_REPLY_TIMEOUT_MS) {
      if (ntpUdp.parsePacket() >= 48) {
        uint32_t recvUs = micros();
        uint8_t reply[48];
        ntpUdp.read(reply, sizeof(reply));
        uint8_t mode = reply[0] & 0x07;
        uint8_t stratum = reply[1];
        uint32_t rttUs = recvUs - sentUs;
        if (mode == 4 && stratum > 0 && stratum < 16 && rttUs < bestRttUs) {
          uint32_t secs = be32(&reply[40]);       // transmit timestamp
          uint32_t frac = be32(&reply[44]);
          bestEpochUs = (int64_t)(secs - 2208988800UL) * 1000000LL
                      + (int64_t)(((uint64_t)frac * 1000000ULL) >> 32)
                      + (int64_t)(rttUs / 2);
          bestRttUs = rttUs;
          bestRecvUs = recvUs;
          have = true;
        }
        break;
      }
      delay(1);
    }
    delay(NTP_SAMPLE_GAP_MS);
  }
  ntpUdp.stop();
  if (!have) {
    return false;
  }
  int64_t nowUs = bestEpochUs + (int64_t)(uint32_t)(micros() - bestRecvUs);
  struct timeval tv;
  tv.tv_sec = (time_t)(nowUs / 1000000LL);
  tv.tv_usec = (suseconds_t)(nowUs % 1000000LL);
  settimeofday(&tv, NULL);
  syncRttUs = bestRttUs;
  return true;
}

void syncClock() {
  Serial.printf("[NTP] Synchronizing clock with %s", NTP_SERVER);
  timeSynced = ntpSync();
  tSync = millis();
  if (timeSynced) {
    Serial.printf(" ok (round trip %.2f ms)\n", syncRttUs / 1000.0);
  } else {
    Serial.println(" FAILED (timing will be marked unsynced)");
  }
}

// ---------- Network ----------

void onNetEvent(arduino_event_id_t event) {
  switch (event) {
    case ARDUINO_EVENT_ETH_START:
      ETH.setHostname(DEVICE_ID);
      break;
    case ARDUINO_EVENT_ETH_CONNECTED:
      Serial.println("[ETH] Link UP");
      break;
    case ARDUINO_EVENT_ETH_GOT_IP:
      if (tLink == 0) {
        tLink = millis();   // first IP address since boot
      }
      ethGotIP = true;
      Serial.printf("[ETH] IP: %s  MAC: %s  %uMbps\n",
                    ETH.localIP().toString().c_str(),
                    ETH.macAddress().c_str(), ETH.linkSpeed());
      break;
    case ARDUINO_EVENT_ETH_DISCONNECTED:
    case ARDUINO_EVENT_ETH_STOP:
      Serial.println("[ETH] Link DOWN");
      ethGotIP = false;
      registered = false;
      break;
    default:
      break;
  }
}

void buildTopics() {
  String base = String(TOPIC_PREFIX) + "/devices/";
  topicRegister  = base + "register";
  topicState     = base + DEVICE_ID + "/state";
  topicHeartbeat = base + DEVICE_ID + "/heartbeat";
  topicData      = base + DEVICE_ID + "/data";
  topicCommand   = base + DEVICE_ID + "/command";
  topicResponse  = base + DEVICE_ID + "/response";
}

void publishJson(const String& topic, JsonDocument& doc, bool retain = false) {
  String out;
  serializeJson(doc, out);
  mqtt.publish(topic.c_str(), out.c_str(), retain);
}

void updateState(const char* state) {
  JsonDocument doc;
  doc["device_id"] = DEVICE_ID;
  doc["state"] = state;
  String out;
  serializeJson(doc, out);
  mqtt.publish(topicState.c_str(), out.c_str(), true);   // retained
}

void registerDevice() {
  JsonDocument doc;
  doc["id"] = DEVICE_ID;
  doc["hardware"] = HARDWARE_TYPE;
  doc["ip"] = ETH.localIP().toString();
  doc["location"] = LOCATION;
  doc["link_type"] = LINK_TYPE;
  doc["protocol"] = "mqtt";
  doc["connection_type"] = "ethernet";
  doc["memory_total"] = ESP.getHeapSize();
  doc["memory_free"] = ESP.getFreeHeap();
  doc["cpu_freq_mhz"] = ESP.getCpuFreqMHz();

  // Measurement block (removed by the bridge before it reaches the server)
  registrationSeq++;
  JsonObject timing = doc["timing"].to<JsonObject>();
  timing["seq"] = registrationSeq;
  timing["synced"] = timeSynced;
  timing["setup_ms"] = tSetup;
  timing["serial_ms"] = tSerial;
  timing["link_ms"] = (unsigned long)tLink;
  timing["sync_ms"] = tSync;
  timing["sync_rtt_ms"] = syncRttUs / 1000.0;
  timing["mqtt_ms"] = tMqtt;
  timing["publish_ms"] = millis();
  timing["epoch_publish_ms"] = epochString(epochMs());   // last field set before publishing

  publishJson(topicRegister, doc);
  registered = true;
  Serial.println("[MQTT] Registration published");
}

void sendHeartbeat() {
  JsonDocument doc;
  doc["device_id"] = DEVICE_ID;
  doc["timestamp"] = millis();
  JsonObject st = doc["status"].to<JsonObject>();
  st["free_heap"] = ESP.getFreeHeap();
  st["link_up"] = ETH.linkUp();
  publishJson(topicHeartbeat, doc);
}

void sendData() {
  JsonDocument doc;
  doc["device_id"] = DEVICE_ID;
  doc["timestamp"] = millis();
  JsonObject d = doc["data"].to<JsonObject>();
  d["temperature"] = temperatureRead();   // internal chip temperature
  d["uptime_s"] = millis() / 1000;
  publishJson(topicData, doc);
}

void onMessage(char* topic, byte* payload, unsigned int length) {
  lastRecvEpochMs = epochMs();   // clock at reception, before parsing (time_probe)

  JsonDocument in;
  if (deserializeJson(in, payload, length)) return;
  const char* cmd = in["command"] | "";

  JsonDocument out;
  out["device_id"] = DEVICE_ID;
  out["command"] = cmd;
  out["status"] = "completed";

  if (strcmp(cmd, "time_probe") == 0) {
    // Four-timestamp clock probe: t1 (sender's clock at send) is echoed,
    // t2 = this board's clock at reception, t3 = this board's clock at reply.
    out["t1"] = String(in["params"]["t1"] | "");
    out["probe_id"] = String(in["params"]["probe_id"] | "");
    out["t2"] = epochString(lastRecvEpochMs);
    out["synced"] = timeSynced;
    out["status"] = "ok";
    out["t3"] = epochString(epochMs());   // last, just before sending
  } else if (strcmp(cmd, "ping") == 0) {
    out["result"] = "pong";
  } else if (strcmp(cmd, "get_status") == 0) {
    out["result"]["free_heap"] = ESP.getFreeHeap();
    out["result"]["uptime_s"] = millis() / 1000;
  } else if (strcmp(cmd, "set_state") == 0) {
    updateState(in["params"]["state"] | "active");
  } else if (strcmp(cmd, "restart") == 0) {
    publishJson(topicResponse, out);
    delay(200);
    ESP.restart();
  } else {
    out["status"] = "unknown";
  }
  publishJson(topicResponse, out);
}

bool connectMQTT() {
  String lwt;
  {
    JsonDocument doc;
    doc["device_id"] = DEVICE_ID;
    doc["state"] = "inactive";
    doc["reason"] = "lwt";
    serializeJson(doc, lwt);
  }
  Serial.printf("[MQTT] Connecting to %s:%u ...\n", MQTT_BROKER, MQTT_PORT);
  if (!mqtt.connect(DEVICE_ID, nullptr, nullptr,
                    topicState.c_str(), 1, true, lwt.c_str())) {
    Serial.printf("[MQTT] Failed, rc=%d\n", mqtt.state());
    return false;
  }
  tMqtt = millis();
  Serial.println("[MQTT] Connected");
  mqtt.subscribe(topicCommand.c_str(), 1);
  updateState("active");
  registerDevice();
  return true;
}

void setup() {
  tSetup = millis();
  Serial.begin(115200);
  delay(500);
  tSerial = millis();
  Serial.println("\n=== Olimex ESP32-PoE | MQTT over Ethernet ===");

  buildTopics();
  WiFi.onEvent(onNetEvent);
  ETH.begin(ETH_PHY_TYPE_OLX, ETH_PHY_ADDR_OLX, ETH_PHY_MDC_OLX,
            ETH_PHY_MDIO_OLX, ETH_PHY_POWER_OLX, ETH_CLK_MODE_OLX);

  mqtt.setServer(MQTT_BROKER, MQTT_PORT);
  mqtt.setKeepAlive(MQTT_KEEPALIVE_SECONDS);
  mqtt.setBufferSize(1024);
  mqtt.setCallback(onMessage);
}

void loop() {
  if (!ethGotIP) {
    // 20 ms (was 200 ms), so MQTT does not start later than needed after the IP.
    delay(20);
    return;
  }

  // Once per boot, with the IP already assigned: synchronize the clock
  if (!clockTried) {
    clockTried = true;
    syncClock();
  }

  if (!mqtt.connected()) {
    registered = false;
    // The first attempt is immediate, then every 5 s. Before, the first one
    // waited until millis() passed 5 s, which added seconds to the onboarding.
    if (lastReconnect == 0 || millis() - lastReconnect > 5000) {
      lastReconnect = millis();
      connectMQTT();
    }
    return;
  }

  mqtt.loop();

  if (millis() - lastHeartbeat > HEARTBEAT_MS) {
    lastHeartbeat = millis();
    sendHeartbeat();
  }
  if (millis() - lastData > DATA_MS) {
    lastData = millis();
    sendData();
  }
}
