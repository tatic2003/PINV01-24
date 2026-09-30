/**
 * IoT-ITS Client Library for ESP32
 * Handles device registration and communication with the ITS server.
 *
 * Project: PINV01-24 - FIUNA
 *
 * Usage:
 *   ITSClient client("192.168.1.1", 5000);
 *   client.setDeviceInfo("esp32-wifi-01", "ESP32-WROOM32");
 *   client.begin();
 *   client.registerDevice();
 *
 *   // In loop:
 *   client.sendHeartbeat();
 */

#ifndef ITS_CLIENT_H
#define ITS_CLIENT_H

#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>

// Configuration defaults
#define ITS_DEFAULT_PORT 5000
#define ITS_HEARTBEAT_INTERVAL 30000  // 30 seconds
#define ITS_RETRY_INTERVAL 5000       // 5 seconds
#define ITS_MAX_RETRIES 3

// Device states
enum ITSDeviceState {
    ITS_STATE_UNKNOWN,
    ITS_STATE_CONNECTING,
    ITS_STATE_REGISTERED,
    ITS_STATE_ACTIVE,
    ITS_STATE_INACTIVE,
    ITS_STATE_ERROR
};

// Classification result
struct ITSClassification {
    String deviceType;
    float confidence;
    String suggestedRole;
    bool valid;
};

class ITSClient {
public:
    /**
     * Constructor
     * @param serverHost ITS server hostname or IP
     * @param serverPort ITS server port (default: 5000)
     */
    ITSClient(const char* serverHost, uint16_t serverPort = ITS_DEFAULT_PORT);

    /**
     * Set device information
     * @param deviceId Unique device identifier
     * @param hardwareType Hardware type string (e.g., "ESP32-WROOM32")
     */
    void setDeviceInfo(const char* deviceId, const char* hardwareType);

    /**
     * Set optional device metadata
     * @param location Device location string
     * @param firmwareVersion Firmware version string
     */
    void setMetadata(const char* location = nullptr, const char* firmwareVersion = nullptr);

    /**
     * Initialize the client
     * Call after WiFi is connected
     */
    void begin();

    /**
     * Register device with the ITS server
     * @return true if registration successful
     */
    bool registerDevice();

    /**
     * Send heartbeat to server
     * @return true if heartbeat successful
     */
    bool sendHeartbeat();

    /**
     * Update device state on server
     * @param state New state string ("active", "inactive", "configuring", "error")
     * @return true if update successful
     */
    bool updateState(const char* state);

    /**
     * Process client tasks (call in loop)
     * Handles automatic heartbeat sending
     */
    void loop();

    /**
     * Get current device state
     */
    ITSDeviceState getState() { return _state; }

    /**
     * Get last classification result
     */
    ITSClassification getClassification() { return _classification; }

    /**
     * Check if device is registered
     */
    bool isRegistered() { return _isRegistered; }

    /**
     * Get server URL
     */
    String getServerUrl() { return _serverUrl; }

    /**
     * Set heartbeat interval in milliseconds
     */
    void setHeartbeatInterval(unsigned long interval) { _heartbeatInterval = interval; }

    /**
     * Enable/disable auto heartbeat
     */
    void setAutoHeartbeat(bool enabled) { _autoHeartbeat = enabled; }

    /**
     * Get last error message
     */
    String getLastError() { return _lastError; }

private:
    String _serverHost;
    uint16_t _serverPort;
    String _serverUrl;

    String _deviceId;
    String _hardwareType;
    String _location;
    String _firmwareVersion;

    ITSDeviceState _state;
    ITSClassification _classification;
    bool _isRegistered;

    unsigned long _lastHeartbeat;
    unsigned long _heartbeatInterval;
    bool _autoHeartbeat;

    String _lastError;

    // HTTP methods
    bool _httpPost(const char* endpoint, const String& payload, String& response);
    bool _httpGet(const char* endpoint, String& response);
    bool _httpPut(const char* endpoint, const String& payload, String& response);

    // JSON helpers
    String _buildRegistrationJson();
    bool _parseClassification(const String& json);

    // Error handling
    void _setError(const char* error);
};

#endif // ITS_CLIENT_H
