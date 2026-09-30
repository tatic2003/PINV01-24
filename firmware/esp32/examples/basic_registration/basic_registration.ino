/**
 * Basic Device Registration Example
 * Demonstrates how to register an ESP32 with the ITS server.
 *
 * Project: PINV01-24 - FIUNA
 *
 * Hardware: ESP32-WROOM32 or compatible
 *
 * Instructions:
 * 1. Update WiFi credentials below
 * 2. Update ITS server IP address
 * 3. Upload to ESP32
 * 4. Monitor serial output (115200 baud)
 */

#include <WiFi.h>
#include "its_client.h"

// ========== Configuration ==========

// WiFi credentials
const char* WIFI_SSID = "YOUR_WIFI_SSID";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";

// ITS Server (Raspberry Pi)
const char* ITS_SERVER = "192.168.1.1";  // Update with your server IP
const uint16_t ITS_PORT = 5000;

// Device info
const char* DEVICE_ID = "esp32-wifi-01";
const char* HARDWARE_TYPE = "ESP32-WROOM32";
const char* LOCATION = "Lab-Test";
const char* FIRMWARE_VERSION = "1.0.0";

// ========== Global Objects ==========

ITSClient itsClient(ITS_SERVER, ITS_PORT);

// ========== Setup ==========

void setup() {
    Serial.begin(115200);
    delay(1000);

    Serial.println("\n========================================");
    Serial.println(" IoT-ITS Device Registration Example");
    Serial.println(" Project: PINV01-24 - FIUNA");
    Serial.println("========================================\n");

    // Connect to WiFi
    connectWiFi();

    // Initialize ITS client
    itsClient.setDeviceInfo(DEVICE_ID, HARDWARE_TYPE);
    itsClient.setMetadata(LOCATION, FIRMWARE_VERSION);
    itsClient.begin();

    // Register with server
    Serial.println("\nAttempting to register with ITS server...");

    int retries = 0;
    while (!itsClient.registerDevice() && retries < 5) {
        Serial.println("Registration failed, retrying in 5 seconds...");
        delay(5000);
        retries++;
    }

    if (itsClient.isRegistered()) {
        Serial.println("\n--- Registration Successful ---");
        printDeviceInfo();
    } else {
        Serial.println("\n--- Registration Failed ---");
        Serial.println("Check server connection and try again.");
    }
}

// ========== Loop ==========

void loop() {
    // Process ITS client tasks (sends heartbeat automatically)
    itsClient.loop();

    // Check WiFi connection
    if (WiFi.status() != WL_CONNECTED) {
        Serial.println("WiFi disconnected, reconnecting...");
        connectWiFi();

        // Re-register after reconnection
        if (WiFi.status() == WL_CONNECTED) {
            itsClient.registerDevice();
        }
    }

    // Example: Print status every 60 seconds
    static unsigned long lastStatus = 0;
    if (millis() - lastStatus > 60000) {
        printStatus();
        lastStatus = millis();
    }

    delay(100);
}

// ========== Helper Functions ==========

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

void printDeviceInfo() {
    Serial.println("\nDevice Information:");
    Serial.print("  Device ID: ");
    Serial.println(DEVICE_ID);
    Serial.print("  Hardware:  ");
    Serial.println(HARDWARE_TYPE);
    Serial.print("  IP:        ");
    Serial.println(WiFi.localIP());
    Serial.print("  Server:    ");
    Serial.println(itsClient.getServerUrl());

    // Print classification if available
    ITSClassification classification = itsClient.getClassification();
    if (classification.valid) {
        Serial.println("\nClassification Result:");
        Serial.print("  Type:       ");
        Serial.println(classification.deviceType);
        Serial.print("  Confidence: ");
        Serial.print(classification.confidence * 100, 1);
        Serial.println("%");
        Serial.print("  Role:       ");
        Serial.println(classification.suggestedRole);
    }
}

void printStatus() {
    Serial.println("\n--- Status ---");
    Serial.print("WiFi: ");
    Serial.println(WiFi.status() == WL_CONNECTED ? "Connected" : "Disconnected");
    Serial.print("ITS Registered: ");
    Serial.println(itsClient.isRegistered() ? "Yes" : "No");
    Serial.print("Free Heap: ");
    Serial.print(ESP.getFreeHeap());
    Serial.println(" bytes");
    Serial.println("--------------");
}
