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
 */

#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>

// ========== Configuration ==========

// WiFi credentials: defined in wifi_secrets.h, which git ignores so the
// password never reaches the repository. Template: wifi_secrets.example.h
#include "wifi_secrets.h"

// MQTT Broker (Raspberry Pi)
const char* MQTT_BROKER = "192.168.0.21";  // Raspberry Pi 5 (Mosquitto)
const uint16_t MQTT_PORT = 1883;

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

// ========== Setup ==========

void setup() {
    Serial.begin(115200);
    delay(1000);

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

    Serial.print("Received command: ");
    Serial.println(command);

    StaticJsonDocument<256> response;
    response["command"] = command;
    response["device_id"] = DEVICE_ID;

    if (strcmp(command, "ping") == 0) {
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

    StaticJsonDocument<512> doc;
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

    char buffer[512];
    serializeJson(doc, buffer);

    if (mqttClient.publish(topicRegister.c_str(), buffer)) {
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

    int attempts = 0;
    while (WiFi.status() != WL_CONNECTED && attempts < 30) {
        delay(500);
        Serial.print(".");
        attempts++;
    }

    if (WiFi.status() == WL_CONNECTED) {
        Serial.println("\nWiFi connected!");
        Serial.print("IP Address: ");
        Serial.println(WiFi.localIP());
    } else {
        Serial.println("\nWiFi connection failed!");
    }
}
