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
 * Once it has an IP address, the board synchronizes its clock by SNTP against
 * NTP_SERVER (the Raspberry Pi 5, whose clock is disciplined by the GPS PPS
 * signal). Every registration carries a "timing" block with the millis() value
 * of each boot stage (setup, serial wait, link with IP, clock synchronized,
 * MQTT connected, publish) and the wall-clock time of the publication. The
 * MQTT->HTTP bridge removes the block before forwarding the announcement, so
 * the server sees the usual registration. The "time_probe" command answers
 * with the board's clock at reception and at reply (experiments/clock_probe.py
 * estimates the clock offset). The SNTP step exists only for the measurement
 * and is reported as a separate stage.
 */

#include <ETH.h>
#include <WiFi.h>
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
const unsigned long NTP_TIMEOUT_MS = 15000;
const time_t VALID_EPOCH = 1700000000;      // any time before 2023 = not synchronized

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

static bool ethGotIP = false;
bool registered = false;
unsigned long lastHeartbeat = 0, lastData = 0, lastReconnect = 0;

String topicRegister, topicState, topicHeartbeat, topicData, topicCommand, topicResponse;

// Onboarding marks (millis() at each boot stage; 0 = not reached yet)
unsigned long tSetup = 0, tSerial = 0, tSync = 0, tMqtt = 0;
volatile unsigned long tLink = 0;     // written by the network event handler
bool timeSynced = false;
bool clockTried = false;
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

void syncClock() {
  Serial.printf("[NTP] Synchronizing clock with %s", NTP_SERVER);
  configTime(0, 0, NTP_SERVER);   // UTC, no daylight saving
  unsigned long start = millis();
  while (time(nullptr) < VALID_EPOCH && millis() - start < NTP_TIMEOUT_MS) {
    delay(10);
  }
  timeSynced = time(nullptr) >= VALID_EPOCH;
  tSync = millis();
  Serial.println(timeSynced ? " ok" : " FAILED (timing will be marked unsynced)");
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
