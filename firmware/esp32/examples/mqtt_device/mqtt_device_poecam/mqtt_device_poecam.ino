/**
 * MQTT Device Example - M5Stack Unit PoE CAM-W (Ethernet / W5500)
 *
 * Same announcement format as mqtt_device_wroom32.ino, but the board is attached over
 * ETHERNET (W5500 on SPI), not WiFi. It announces link_type = "ethernet" and
 * protocol = "mqtt" as two separate fields, so the server can tell this board
 * apart from a WiFi ESP32 even though both are ESP32-class and speak MQTT.
 *
 * Project: PINV01-24 - FIUNA
 *
 * Hardware: M5Stack Unit PoE CAM-W (ESP32-D0WDQ6-V3, W5500, 16 MB flash)
 *
 * W5500 pins (taken from the official M5PoECAM library, M5PoECAM.h):
 *   CS = G4, MOSI = G13, MISO = G38, SCK = G23
 *
 * Libraries (Arduino Library Manager):
 *   - M5_Ethernet   (M5Stack's W5500 Ethernet library; same one used by the
 *                    official M5PoECAM web_cam/eth example)
 *   - PubSubClient
 *   - ArduinoJson   (v6 API: StaticJsonDocument)
 *
 * Board: the official example targets the "M5Stack" board package v2.0.9.
 * A generic "ESP32 Dev Module" (Flash Size 16MB) is enough for this sketch,
 * because it does not use the camera.
 *
 * Instructions:
 *   1. Set MQTT_BROKER below to the IP of the machine running Mosquitto.
 *   2. Upload (Upload Speed 115200 is the safest through jumper wires).
 *   3. Open the Serial Monitor at 115200 baud.
 *
 *
 * Onboarding-latency instrumentation
 * ----------------------------------
 * After DHCP, the board sets its clock from NTP_SERVER (the Raspberry Pi 5,
 * disciplined by the GPS PPS signal). The W5500 has its own TCP/IP stack,
 * outside the ESP32's network stack, so the ESP-IDF SNTP client cannot reach
 * it: a minimal NTP client runs over EthernetUDP instead. It sends NTP_SAMPLES
 * requests and sets the clock from the one with the shortest round trip
 * (server transmit time + half the round trip), the same method as the other
 * boards. Every registration then carries a "timing" block (millis() of each
 * boot stage, the round trip of the NTP sample used and the wall-clock time of
 * the publication), which the MQTT->HTTP bridge removes before forwarding. The "time_probe" command answers with the
 * board's clock at reception and at reply (experiments/clock_probe.py).
 * The NTP step exists only for the measurement and is reported separately.
 */

#include <SPI.h>
#include <M5_Ethernet.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include "esp_system.h"
#include <time.h>
#include <sys/time.h>
// esp_read_mac / ESP_MAC_ETH live in esp_mac.h on ESP32 Arduino core 3.x
// (ESP-IDF 5); on core 2.x they come with esp_system.h and this file is absent.
#if __has_include("esp_mac.h")
#include "esp_mac.h"
#endif

// ========== Configuration ==========

// MQTT Broker (the machine running Mosquitto, on the same LAN)
const char* MQTT_BROKER = "192.168.0.21";  // <-- verify: IP of your broker
const uint16_t MQTT_PORT = 1883;

// NTP server for the onboarding-latency measurement: the Pi 5 itself
// (chrony must allow the LAN: "allow 192.168.0.0/24").
const char* NTP_SERVER = "192.168.0.21";
const uint16_t NTP_LOCAL_PORT = 8123;
const int NTP_SAMPLES = 4;                      // requests; the shortest round trip wins
const unsigned long NTP_REPLY_TIMEOUT_MS = 500;
const unsigned long NTP_SAMPLE_GAP_MS = 50;

// MQTT keepalive, in seconds. The broker declares this client dead (and
// publishes its Last Will) after roughly 1.5 x this value, so it sets how
// fast the system notices an ungraceful disconnection (e.g. cable unplugged).
const uint16_t MQTT_KEEPALIVE_SECONDS = 15;

const char* MQTT_USER = nullptr;  // Set if authentication required
const char* MQTT_PASS = nullptr;

// Device info
// Device id = DEVICE_ID_BASE + "-" + last 3 bytes of the factory MAC, built in
// setup(). Same scheme as the Wi-Fi sketches: stable and unique per chip.
const char* DEVICE_ID_BASE = "esp32-poecam";
String deviceIdStr;
const char* DEVICE_ID = "";
const char* HARDWARE_TYPE = "ESP32-PoECAM";   // contains "esp32" -> ESP32Device
const char* LOCATION      = "Lab-Test";

// Physical link of this board. This is NOT the protocol: the board speaks
// MQTT over this link.
const char* LINK_TYPE = "ethernet";

// Topic prefix
const char* TOPIC_PREFIX = "its";

// W5500 SPI pins (M5 Unit PoE CAM-W)
const int ETH_CS_PIN   = 4;
const int ETH_MOSI_PIN = 13;
const int ETH_MISO_PIN = 38;
const int ETH_SCK_PIN  = 23;

// ========== Global Objects ==========

EthernetClient ethClient;
PubSubClient mqttClient(ethClient);
EthernetUDP ntpUdp;

uint8_t macAddress[6];

// Topics
String topicRegister;
String topicHeartbeat;
String topicState;
String topicData;
String topicCommand;
String topicResponse;

// State
bool registered = false;
unsigned long lastHeartbeat = 0;
const unsigned long HEARTBEAT_INTERVAL = 30000;  // 30 seconds
unsigned long lastConnectAttempt = 0;
const unsigned long RECONNECT_INTERVAL = 5000;   // 5 seconds

// Onboarding timing (millis() at each boot stage; 0 = not reached yet)
unsigned long tSetup = 0;
unsigned long tSerial = 0;
unsigned long tLink = 0;      // link up and address assigned by DHCP
unsigned long tSync = 0;
unsigned long tMqtt = 0;
bool timeSynced = false;
uint32_t syncRttUs = 0;           // round trip of the NTP sample that set the clock
uint32_t registrationSeq = 0;     // 1 = first registration since boot
int64_t lastRecvEpochMs = 0;      // clock when the last MQTT message arrived

// ========== Helpers ==========

bool linkIsUp() {
    return Ethernet.linkStatus() == LinkON;
}

String ipToString(const IPAddress& ip) {
    char buf[16];
    snprintf(buf, sizeof(buf), "%u.%u.%u.%u", ip[0], ip[1], ip[2], ip[3]);
    return String(buf);
}

// ========== Clock ==========

// Wall-clock time in milliseconds since the epoch (0 before the NTP step).
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

// Minimal NTP client over the W5500: NTP_SAMPLES requests; the clock is set
// from the reply with the shortest round trip, as the server's transmit time
// plus half that round trip, advanced by the time elapsed since the reply.
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
            ntpUdp.flush();               // drop stale replies
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
    Serial.print("Synchronizing clock with ");
    Serial.print(NTP_SERVER);
    timeSynced = ntpSync();
    tSync = millis();
    if (timeSynced) {
        Serial.printf(" ok (round trip %.2f ms)\n", syncRttUs / 1000.0);
    } else {
        Serial.println(" FAILED (timing will be marked unsynced)");
    }
}

// ========== Setup ==========

void setup() {
    tSetup = millis();
    Serial.begin(115200);
    delay(1000);
    tSerial = millis();

    Serial.println("\n========================================");
    Serial.println(" IoT-ITS MQTT Device - PoE CAM-W (Ethernet)");
    Serial.println(" Project: PINV01-24 - FIUNA");
    Serial.println("========================================\n");

        // Unique, stable device id from the factory MAC (eFuse)
    uint64_t mac = ESP.getEfuseMac();
    char suffix[7];
    snprintf(suffix, sizeof(suffix), "%02x%02x%02x",
             (unsigned)((mac >> 24) & 0xFF),
             (unsigned)((mac >> 32) & 0xFF),
             (unsigned)((mac >> 40) & 0xFF));
    deviceIdStr = String(DEVICE_ID_BASE) + "-" + suffix;
    DEVICE_ID = deviceIdStr.c_str();
    Serial.print("Device ID: ");
    Serial.println(DEVICE_ID);
    
    buildTopics();
    connectEthernet();

    // Synchronize the clock (measurement only)
    if (Ethernet.localIP() != IPAddress(0, 0, 0, 0)) {
        syncClock();
    }

    mqttClient.setServer(MQTT_BROKER, MQTT_PORT);
    mqttClient.setCallback(mqttCallback);
    mqttClient.setBufferSize(1024);
}

// ========== Loop ==========

void loop() {
    // Keep the DHCP lease alive.
    Ethernet.maintain();

    if (!linkIsUp()) {
        // Cable unplugged or switch down. Do not try MQTT; the broker will
        // publish our Last Will on its own once keepalive expires.
        if (registered) {
            Serial.println("Ethernet link DOWN");
            registered = false;
        }
        if (mqttClient.connected()) {
            mqttClient.disconnect();
        }
        delay(500);
        return;
    }

    if (!mqttClient.connected()) {
        if (millis() - lastConnectAttempt >= RECONNECT_INTERVAL ||
            lastConnectAttempt == 0) {
            lastConnectAttempt = millis();
            connectMQTT();
        }
    } else {
        mqttClient.loop();
    }

    // Heartbeat
    if (registered && millis() - lastHeartbeat >= HEARTBEAT_INTERVAL) {
        sendHeartbeat();
        lastHeartbeat = millis();
    }

    // Example data every 60 seconds (replace with real readings)
    static unsigned long lastDataSend = 0;
    if (registered && millis() - lastDataSend >= 60000) {
        sendSensorData();
        lastDataSend = millis();
    }

    delay(10);
}

// ========== Topic Management ==========

void buildTopics() {
    String prefix = String(TOPIC_PREFIX);
    String deviceId = String(DEVICE_ID);

    topicRegister  = prefix + "/devices/register";
    topicHeartbeat = prefix + "/devices/" + deviceId + "/heartbeat";
    topicState     = prefix + "/devices/" + deviceId + "/state";
    topicData      = prefix + "/devices/" + deviceId + "/data";
    topicCommand   = prefix + "/devices/" + deviceId + "/command";
    topicResponse  = prefix + "/devices/" + deviceId + "/response";
}

// ========== Ethernet ==========

void connectEthernet() {
    // Unique, valid MAC derived from the ESP32's factory-programmed MAC.
    esp_read_mac(macAddress, ESP_MAC_ETH);

    SPI.begin(ETH_SCK_PIN, ETH_MISO_PIN, ETH_MOSI_PIN, -1);
    Ethernet.init(ETH_CS_PIN);

    Serial.print("W5500 MAC: ");
    for (int i = 0; i < 6; i++) {
        if (macAddress[i] < 16) Serial.print("0");
        Serial.print(macAddress[i], HEX);
        if (i < 5) Serial.print(":");
    }
    Serial.println();

    // Wait for a cable. Without a link there is nothing to configure.
    Serial.print("Waiting for Ethernet link");
    unsigned long start = millis();
    // Polled every 50 ms (was 500 ms) so the link-up time has a 50 ms resolution.
    int dots = 0;
    while (!linkIsUp() && millis() - start < 30000) {
        if (dots++ % 10 == 0) {
            Serial.print(".");
        }
        delay(50);
    }
    Serial.println();

    if (!linkIsUp()) {
        Serial.println("No Ethernet link yet. Check the cable / PoE power.");
        Serial.println("Will keep trying from loop().");
    }

    // Get an address via DHCP (same call as the official example).
    int attempts = 0;
    while (Ethernet.begin(macAddress) != 1 && attempts < 5) {
        Serial.println("Error getting IP address via DHCP, trying again...");
        delay(2000);
        attempts++;
    }

    if (Ethernet.localIP() == IPAddress(0, 0, 0, 0)) {
        Serial.println("DHCP failed. Is the cable connected to the router?");
    } else {
        if (tLink == 0) {
            tLink = millis();   // first address since boot
        }
        Serial.print("IP address: ");
        Serial.println(ipToString(Ethernet.localIP()));
    }
}

// ========== MQTT Functions ==========

void connectMQTT() {
    if (Ethernet.localIP() == IPAddress(0, 0, 0, 0)) {
        // No address yet: retry DHCP before touching MQTT.
        if (Ethernet.begin(macAddress) != 1) {
            Serial.println("DHCP still failing");
            return;
        }
        Serial.print("IP address: ");
        Serial.println(ipToString(Ethernet.localIP()));
    }

    Serial.print("Connecting to MQTT broker ");
    Serial.print(MQTT_BROKER);
    Serial.print("...");

    String clientId = "esp32-" + String(DEVICE_ID);

    // Last Will and Testament: the broker publishes this on our behalf if we
    // disappear without disconnecting cleanly (power loss, crash, cable
    // unplugged). It goes to the ordinary state topic and is retained.
    StaticJsonDocument<192> willDoc;
    willDoc["device_id"] = DEVICE_ID;
    willDoc["state"] = "inactive";
    willDoc["reason"] = "lwt";
    char willPayload[192];
    serializeJson(willDoc, willPayload);

    mqttClient.setKeepAlive(MQTT_KEEPALIVE_SECONDS);

    bool connected;
    if (MQTT_USER) {
        connected = mqttClient.connect(
            clientId.c_str(), MQTT_USER, MQTT_PASS,
            topicState.c_str(), 1, true, willPayload);
    } else {
        connected = mqttClient.connect(
            clientId.c_str(), NULL, NULL,
            topicState.c_str(), 1, true, willPayload);
    }

    if (connected) {
        tMqtt = millis();
        Serial.println(" connected!");
        Serial.print("Last Will registered on: ");
        Serial.println(topicState);

        mqttClient.subscribe(topicCommand.c_str());
        Serial.print("Subscribed to: ");
        Serial.println(topicCommand);

        // Overwrite any retained "inactive" left by a previous session.
        updateState("active");

        registerDevice();
    } else {
        Serial.print(" failed, rc=");
        Serial.println(mqttClient.state());
    }
}

void mqttCallback(char* topic, byte* payload, unsigned int length) {
    // Clock at reception, before any parsing (used by time_probe)
    lastRecvEpochMs = epochMs();

    StaticJsonDocument<512> doc;
    DeserializationError error = deserializeJson(doc, payload, length);

    if (error) {
        Serial.println("Failed to parse MQTT message");
        return;
    }

    String topicStr = String(topic);
    Serial.print("Message on topic: ");
    Serial.println(topicStr);

    if (topicStr == topicCommand) {
        handleCommand(doc);
    }
}

void handleCommand(JsonDocument& doc) {
    const char* command = doc["command"];
    JsonObject params = doc["params"];

    if (command == nullptr) {
        Serial.println("Command message without 'command'");
        return;
    }

    Serial.print("Received command: ");
    Serial.println(command);

    StaticJsonDocument<384> response;
    response["command"] = command;
    response["device_id"] = DEVICE_ID;

    if (strcmp(command, "time_probe") == 0) {
        // Four-timestamp clock probe: t1 (sender's clock at send) is echoed,
        // t2 = this board's clock at reception, t3 = at reply.
        const char* t1 = params["t1"] | "";
        const char* probeId = params["probe_id"] | "";
        response["t1"] = String(t1);
        response["probe_id"] = String(probeId);
        response["t2"] = epochString(lastRecvEpochMs);
        response["synced"] = timeSynced;
        response["status"] = "ok";
        response["t3"] = epochString(epochMs());   // last, just before sending
    }
    else if (strcmp(command, "ping") == 0) {
        response["status"] = "pong";
    }
    else if (strcmp(command, "restart") == 0) {
        response["status"] = "restarting";
        sendResponse(response);
        delay(1000);
        ESP.restart();
    }
    else if (strcmp(command, "get_status") == 0) {
        response["status"] = "ok";
        response["free_heap"] = ESP.getFreeHeap();
        response["uptime"] = millis() / 1000;
        response["link_up"] = linkIsUp();
    }
    else if (strcmp(command, "set_state") == 0) {
        const char* newState = params["state"];
        if (newState) {
            updateState(newState);
            response["status"] = "state_updated";
            response["new_state"] = newState;
        } else {
            response["status"] = "error";
            response["error"] = "missing state parameter";
        }
    }
    else {
        response["status"] = "unknown_command";
    }

    sendResponse(response);
}

void sendResponse(JsonDocument& doc) {
    char buffer[512];
    serializeJson(doc, buffer);
    mqttClient.publish(topicResponse.c_str(), buffer);
}

// ========== Device Operations ==========

void registerDevice() {
    Serial.println("Registering device via MQTT...");

    StaticJsonDocument<896> doc;
    doc["id"] = DEVICE_ID;
    doc["hardware"] = HARDWARE_TYPE;
    doc["ip"] = ipToString(Ethernet.localIP());
    doc["location"] = LOCATION;
    // Physical link and application protocol are announced as two separate
    // fields, so an Ethernet board is not announced like a WiFi one.
    doc["link_type"] = LINK_TYPE;
    doc["protocol"] = "mqtt";
    // Kept so older server builds, which only read connection_type, still
    // receive the link.
    doc["connection_type"] = LINK_TYPE;
    doc["memory_total"] = ESP.getHeapSize();
    doc["memory_free"] = ESP.getFreeHeap();
    doc["cpu_freq_mhz"] = ESP.getCpuFreqMHz();

    // Measurement block (removed by the bridge before it reaches the server)
    registrationSeq++;
    JsonObject timing = doc.createNestedObject("timing");
    timing["seq"] = registrationSeq;
    timing["synced"] = timeSynced;
    timing["setup_ms"] = tSetup;
    timing["serial_ms"] = tSerial;
    timing["link_ms"] = tLink;
    timing["sync_ms"] = tSync;
    timing["sync_rtt_ms"] = syncRttUs / 1000.0;
    timing["mqtt_ms"] = tMqtt;
    timing["publish_ms"] = millis();
    timing["epoch_publish_ms"] = epochString(epochMs());   // last field set before publishing

    char buffer[896];
    size_t n = serializeJson(doc, buffer, sizeof(buffer));

    if (n > 0 && mqttClient.publish(topicRegister.c_str(), buffer)) {
        Serial.println("Registration message sent");
        registered = true;
        lastHeartbeat = millis();
        updateState("active");
    } else {
        Serial.println("Registration failed");
    }
}

void sendHeartbeat() {
    StaticJsonDocument<256> doc;
    doc["device_id"] = DEVICE_ID;
    doc["timestamp"] = millis();
    // No wifi_rssi here: this board is on Ethernet.
    doc["status"]["free_heap"] = ESP.getFreeHeap();
    doc["status"]["link_up"] = linkIsUp();

    char buffer[256];
    serializeJson(doc, buffer);

    if (mqttClient.publish(topicHeartbeat.c_str(), buffer)) {
        Serial.println("Heartbeat sent");
    }
}

void updateState(const char* state) {
    StaticJsonDocument<256> doc;
    doc["device_id"] = DEVICE_ID;
    doc["state"] = state;
    doc["timestamp"] = millis();

    char buffer[256];
    serializeJson(doc, buffer);

    // Publish with retain flag
    mqttClient.publish(topicState.c_str(), buffer, true);
    Serial.print("State updated to: ");
    Serial.println(state);
}

void sendSensorData() {
    // Placeholder reading. Replace with real data.
    StaticJsonDocument<256> doc;
    doc["device_id"] = DEVICE_ID;
    doc["timestamp"] = millis();
    doc["data"]["free_heap"] = ESP.getFreeHeap();
    doc["data"]["uptime_s"] = millis() / 1000;

    char buffer[256];
    serializeJson(doc, buffer);

    if (mqttClient.publish(topicData.c_str(), buffer)) {
        Serial.println("Sensor data sent");
    }
}
