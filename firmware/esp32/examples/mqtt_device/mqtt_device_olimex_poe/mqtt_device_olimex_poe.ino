/*
 * mqtt_device_olimex_poe.ino
 * Olimex ESP32-PoE (LAN8720, RMII) -> MQTT por Ethernet, formato de anuncio IoT-ITS (PINV01-24)
 *
 * Arduino IDE:
 *   Placa:  "OLIMEX ESP32-PoE"  (esp32 core 3.x)
 *   Libs:   PubSubClient 2.8, ArduinoJson 7.x
 *
 * IMPORTANTE: no uses USB y PoE al mismo tiempo (placa sin aislamiento).
 */

#include <ETH.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>

// ---------- Configuracion ----------
const char* MQTT_BROKER = "192.168.0.21";   // IP real del Mosquitto
const uint16_t MQTT_PORT = 1883;
const uint16_t MQTT_KEEPALIVE_SECONDS = 15;

const char* DEVICE_ID     = "esp32-olimex-poe-01";
const char* HARDWARE_TYPE = "ESP32-PoE-Olimex";   // contiene 'esp32'
const char* LOCATION      = "Lab-Test";
const char* LINK_TYPE     = "ethernet";
const char* TOPIC_PREFIX  = "its";

const unsigned long HEARTBEAT_MS = 30000;
const unsigned long DATA_MS      = 60000;

// Pines Ethernet de la Olimex ESP32-PoE (LAN8720)
#define ETH_PHY_TYPE_OLX  ETH_PHY_LAN8720
#define ETH_PHY_ADDR_OLX  0
#define ETH_PHY_MDC_OLX   23
#define ETH_PHY_MDIO_OLX  18
#define ETH_PHY_POWER_OLX 12
#define ETH_CLK_MODE_OLX  ETH_CLOCK_GPIO17_OUT

// ---------- Estado ----------
WiFiClient netClient;
PubSubClient mqtt(netClient);

static bool ethGotIP = false;
bool registered = false;
unsigned long lastHeartbeat = 0, lastData = 0, lastReconnect = 0;

String topicRegister, topicState, topicHeartbeat, topicData, topicCommand, topicResponse;

void onNetEvent(arduino_event_id_t event) {
  switch (event) {
    case ARDUINO_EVENT_ETH_START:
      ETH.setHostname(DEVICE_ID);
      break;
    case ARDUINO_EVENT_ETH_CONNECTED:
      Serial.println("[ETH] Link UP");
      break;
    case ARDUINO_EVENT_ETH_GOT_IP:
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
  mqtt.publish(topicState.c_str(), out.c_str(), true);   // retenido
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
  publishJson(topicRegister, doc);
  registered = true;
  Serial.println("[MQTT] Registro publicado");
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
  d["temperature"] = temperatureRead();   // temperatura interna del chip
  d["uptime_s"] = millis() / 1000;
  publishJson(topicData, doc);
}

void onMessage(char* topic, byte* payload, unsigned int length) {
  JsonDocument in;
  if (deserializeJson(in, payload, length)) return;
  const char* cmd = in["command"] | "";

  JsonDocument out;
  out["device_id"] = DEVICE_ID;
  out["command"] = cmd;
  out["status"] = "completed";

  if (strcmp(cmd, "ping") == 0) {
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
  Serial.printf("[MQTT] Conectando a %s:%u ...\n", MQTT_BROKER, MQTT_PORT);
  if (!mqtt.connect(DEVICE_ID, nullptr, nullptr,
                    topicState.c_str(), 1, true, lwt.c_str())) {
    Serial.printf("[MQTT] Fallo, rc=%d\n", mqtt.state());
    return false;
  }
  Serial.println("[MQTT] Conectado");
  mqtt.subscribe(topicCommand.c_str(), 1);
  updateState("active");
  registerDevice();
  return true;
}

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\n=== Olimex ESP32-PoE | MQTT por Ethernet ===");

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
    delay(200);
    return;
  }

  if (!mqtt.connected()) {
    registered = false;
    if (millis() - lastReconnect > 5000) {
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
