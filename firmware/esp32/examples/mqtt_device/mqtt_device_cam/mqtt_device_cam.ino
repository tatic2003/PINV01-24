/**
 * MQTT Device Example
 * Demonstrates ESP32 communication via MQTT with the ITS server.
 *
 * Project: PINV01-24 - FIUNA
 *
 * Hardware: ESP32-WROOM32 or compatible
 *
 * Instructions:
 * 1. Install PubSubClient library via Arduino Library Manager
 * 2. Copy wifi_secrets.example.h to wifi_secrets.h in this sketch folder
 *    and fill in your WiFi name and password (wifi_secrets.h is not committed)
 * 3. Check MQTT_BROKER below (IP of the Raspberry Pi 5 running Mosquitto)
 * 4. Upload to ESP32
 * 5. Monitor serial output (115200 baud)
 *
 * Onboarding-latency instrumentation
 * ----------------------------------
 * After the WiFi link comes up, the board synchronizes its clock by SNTP
 * against NTP_SERVER (the Raspberry Pi 5, whose clock is disciplined by the
 * GPS PPS signal). Every registration then carries a "timing" block with the
 * millis() value of each boot stage (setup, serial wait, link up, clock
 * synchronized, MQTT connected, publish) and the wall-clock time of the
 * publication. The MQTT->HTTP bridge removes the block before forwarding the
 * announcement, so the server sees the usual registration.
 *
 * The "time_probe" command answers with the board's clock at reception and
 * at reply, so experiments/clock_probe.py can estimate the offset of this
 * board's clock with respect to the Pi (NTP-style four timestamps).
 *
 * The SNTP step exists only for the measurement; it is reported as a separate
 * stage so it can be excluded from the onboarding time.
 */

#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <time.h>
#include <sys/time.h>

// ========== Configuration ==========

// WiFi credentials: defined in wifi_secrets.h, which git ignores so the
// password never reaches the repository. Template: wifi_secrets.example.h
#include "wifi_secrets.h"

// MQTT Broker (Raspberry Pi)
const char* MQTT_BROKER = "192.168.0.21";  // Raspberry Pi 5 (Mosquitto)
const uint16_t MQTT_PORT = 1883;

// NTP server for the onboarding-latency measurement: the Pi 5 itself
// (chrony must allow the LAN: "allow 192.168.0.0/24" in chrony.conf).
const char* NTP_SERVER = "192.168.0.21";
const unsigned long NTP_TIMEOUT_MS = 15000;     // give up after this long
const time_t VALID_EPOCH = 1700000000;          // any time before 2023 = not synced

// MQTT keepalive, in seconds. The broker declares this client dead (and
// publishes its Last Will) after roughly 1.5 x this value, so it sets how
// fast the system notices an ungraceful disconnection.
const uint16_t MQTT_KEEPALIVE_SECONDS = 15;

const char* MQTT_USER = nullptr;  // Set if authentication required
const char* MQTT_PASS = nullptr;

// Device info
// Device id = DEVICE_ID_BASE + "-" + last 3 bytes of the factory MAC, built in
// setup(). Several boards of the same model can run this same firmware without
// colliding on the broker (a repeated MQTT client id makes the broker drop the
// older session). The id is stable: it only depends on the chip.
const char* DEVICE_ID_BASE = "esp32-cam";
String deviceIdStr;
const char* DEVICE_ID = "";
const char* HARDWARE_TYPE = "ESP32-CAM";
const char* LOCATION = "Lab-Test";

// Physical link of this board: "wifi" or "ethernet".
// Set to "ethernet" on PoE boards (and use ETH.h instead of WiFi.h there).
// This is NOT the protocol: the board speaks MQTT over this link.
const char* LINK_TYPE = "wifi";

// Topic prefix
const char* TOPIC_PREFIX = "its";

// ========== Global Objects ==========

WiFiClient wifiClient;
PubSubClient mqttClient(wifiClient);

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

// Onboarding timing (millis() at each boot stage; 0 = not reached yet)
unsigned long tSetup = 0;
unsigned long tSerial = 0;
unsigned long tLink = 0;
unsigned long tSync = 0;
unsigned long tMqtt = 0;
bool timeSynced = false;
uint32_t registrationSeq = 0;     // 1 = first registration since boot
int64_t lastRecvEpochMs = 0;      // clock when the last MQTT message arrived

// ========== Clock helpers ==========

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
    Serial.print("Synchronizing clock with ");
    Serial.print(NTP_SERVER);
    configTime(0, 0, NTP_SERVER);   // UTC, no daylight saving
    unsigned long start = millis();
    while (time(nullptr) < VALID_EPOCH && millis() - start < NTP_TIMEOUT_MS) {
        delay(10);
    }
    timeSynced = time(nullptr) >= VALID_EPOCH;
    tSync = millis();
    Serial.println(timeSynced ? " ok" : " FAILED (timing will be marked unsynced)");
}

// ========== Setup ==========

void setup() {
    tSetup = millis();
    Serial.begin(115200);
    delay(1000);
    tSerial = millis();

    Serial.println("\n========================================");
    Serial.println(" IoT-ITS MQTT Device Example");
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

    // Build topic strings
    buildTopics();

    // Connect to WiFi
    connectWiFi();

    // Synchronize the clock (measurement only)
    if (WiFi.status() == WL_CONNECTED) {
        syncClock();
    }

    // Setup MQTT
    mqttClient.setServer(MQTT_BROKER, MQTT_PORT);
    mqttClient.setCallback(mqttCallback);
    mqttClient.setBufferSize(1024);

    // Connect to MQTT
    connectMQTT();
}

// ========== Loop ==========

void loop() {
    // Maintain connections
    if (WiFi.status() != WL_CONNECTED) {
        connectWiFi();
    }

    if (!mqttClient.connected()) {
        connectMQTT();
    }

    mqttClient.loop();

    // Send heartbeat periodically
    if (registered && millis() - lastHeartbeat >= HEARTBEAT_INTERVAL) {
        sendHeartbeat();
        lastHeartbeat = millis();
    }

    // Example: Send sensor data every 60 seconds
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

    topicRegister = prefix + "/devices/register";
    topicHeartbeat = prefix + "/devices/" + deviceId + "/heartbeat";
    topicState = prefix + "/devices/" + deviceId + "/state";
    topicData = prefix + "/devices/" + deviceId + "/data";
    topicCommand = prefix + "/devices/" + deviceId + "/command";
    topicResponse = prefix + "/devices/" + deviceId + "/response";
}

// ========== MQTT Functions ==========

void connectMQTT() {
    Serial.print("Connecting to MQTT broker...");

    String clientId = "esp32-" + String(DEVICE_ID);

    // Last Will and Testament: the broker publishes this on our behalf if we
    // disappear without disconnecting cleanly (power loss, crash, out of
    // range). This is what makes departure detection event-driven: the server
    // learns the device is gone in about 1.5 x keepalive seconds, instead of
    // waiting for missed heartbeats. It is the "unplug" half of Plug-and-Play.
    //
    // It is published to the ordinary state topic, so no extra server-side
    // plumbing is needed, and it is retained so that a subscriber connecting
    // later still sees the device's last known state.
    StaticJsonDocument<192> willDoc;
    willDoc["device_id"] = DEVICE_ID;
    willDoc["state"] = "inactive";
    willDoc["reason"] = "lwt";
    char willPayload[192];
    serializeJson(willDoc, willPayload);

    // Shorter keepalive => faster detection. The broker declares the client
    // dead after roughly 1.5 x this value.
    mqttClient.setKeepAlive(MQTT_KEEPALIVE_SECONDS);

    int attempts = 0;
    while (!mqttClient.connected() && attempts < 10) {
        bool connected;
        if (MQTT_USER) {
            connected = mqttClient.connect(
                clientId.c_str(), MQTT_USER, MQTT_PASS,
                topicState.c_str(),   // will topic
                1,                    // will QoS
                true,                 // will retain
                willPayload           // will message
            );
        } else {
            connected = mqttClient.connect(
                clientId.c_str(), NULL, NULL,
                topicState.c_str(), 1, true, willPayload
            );
        }

        if (connected) {
            tMqtt = millis();
            Serial.println(" connected!");
            Serial.print("Last Will registered on: ");
            Serial.println(topicState);

            // Subscribe to command topic
            mqttClient.subscribe(topicCommand.c_str());
            Serial.print("Subscribed to: ");
            Serial.println(topicCommand);

            // Overwrite any retained "inactive" left by a previous session, so
            // the device's current state is the live one.
            updateState("active");

            // Register device
            registerDevice();
            return;
        }

        Serial.print(".");
        delay(1000);
        attempts++;
    }

    Serial.println(" failed!");
}

void mqttCallback(char* topic, byte* payload, unsigned int length) {
    // Clock at reception, before any parsing (used by time_probe)
    lastRecvEpochMs = epochMs();

    // Parse incoming message
    StaticJsonDocument<512> doc;
    DeserializationError error = deserializeJson(doc, payload, length);

    if (error) {
        Serial.println("Failed to parse MQTT message");
        return;
    }

    String topicStr = String(topic);
    Serial.print("Message on topic: ");
    Serial.println(topicStr);

    // Handle commands
    if (topicStr == topicCommand) {
        handleCommand(doc);
    }
}

void handleCommand(JsonDocument& doc) {
    const char* command = doc["command"];
    JsonObject params = doc["params"];

    if (!command) {
        return;
    }

    Serial.print("Received command: ");
    Serial.println(command);

    StaticJsonDocument<384> response;
    response["command"] = command;
    response["device_id"] = DEVICE_ID;

    if (strcmp(command, "time_probe") == 0) {
        // Four-timestamp clock probe: t1 (sender's clock at send) is echoed,
        // t2 = this board's clock at reception, t3 = this board's clock at
        // reply. The sender adds t4 at reception of the reply.
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
        response["wifi_rssi"] = WiFi.RSSI();
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
    doc["ip"] = WiFi.localIP().toString();
    doc["location"] = LOCATION;
    // The physical link and the application protocol are announced as two
    // separate fields. Reporting only "mqtt" would hide whether the board is
    // attached over WiFi or Ethernet, and an ESP32-PoE would be announced
    // exactly like an ESP32-WROOM.
    doc["link_type"] = LINK_TYPE;
    doc["protocol"] = "mqtt";
    // Kept so that older server builds, which only read connection_type,
    // still receive the link.
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
    timing["mqtt_ms"] = tMqtt;
    timing["publish_ms"] = millis();
    timing["epoch_publish_ms"] = epochString(epochMs());   // last field set before publishing

    char buffer[896];
    size_t n = serializeJson(doc, buffer, sizeof(buffer));

    if (n > 0 && mqttClient.publish(topicRegister.c_str(), buffer)) {
        Serial.println("Registration message sent");
        registered = true;
        lastHeartbeat = millis();

        // Publish initial state
        updateState("active");
    } else {
        Serial.println("Registration failed");
    }
}

void sendHeartbeat() {
    StaticJsonDocument<256> doc;
    doc["device_id"] = DEVICE_ID;
    doc["timestamp"] = millis();
    doc["status"]["wifi_rssi"] = WiFi.RSSI();
    doc["status"]["free_heap"] = ESP.getFreeHeap();

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
    // Example: Read some sensor values
    // Replace with actual sensor readings
    float temperature = 25.0 + (random(100) / 10.0);
    float humidity = 50.0 + (random(200) / 10.0);

    StaticJsonDocument<256> doc;
    doc["device_id"] = DEVICE_ID;
    doc["timestamp"] = millis();
    doc["data"]["temperature"] = temperature;
    doc["data"]["humidity"] = humidity;
    doc["data"]["free_heap"] = ESP.getFreeHeap();

    char buffer[256];
    serializeJson(doc, buffer);

    if (mqttClient.publish(topicData.c_str(), buffer)) {
        Serial.println("Sensor data sent");
    }
}

// ========== WiFi Functions ==========

void connectWiFi() {
    Serial.print("Connecting to WiFi: ");
    Serial.println(WIFI_SSID);

    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

    // Polled every 50 ms (15 s in total, as before) so that the link-up time
    // recorded for the onboarding measurement has a 50 ms resolution.
    int attempts = 0;
    while (WiFi.status() != WL_CONNECTED && attempts < 300) {
        delay(50);
        if (attempts % 10 == 0) {
            Serial.print(".");
        }
        attempts++;
    }

    if (WiFi.status() == WL_CONNECTED) {
        if (tLink == 0) {
            tLink = millis();   // first link since boot
        }
        Serial.println("\nWiFi connected!");
        Serial.print("IP Address: ");
        Serial.println(WiFi.localIP());
    } else {
        Serial.println("\nWiFi connection failed!");
    }
}
