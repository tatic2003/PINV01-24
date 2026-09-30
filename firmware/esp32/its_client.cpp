/**
 * IoT-ITS Client Library Implementation
 * Project: PINV01-24 - FIUNA
 */

#include "its_client.h"

ITSClient::ITSClient(const char* serverHost, uint16_t serverPort)
    : _serverHost(serverHost),
      _serverPort(serverPort),
      _state(ITS_STATE_UNKNOWN),
      _isRegistered(false),
      _lastHeartbeat(0),
      _heartbeatInterval(ITS_HEARTBEAT_INTERVAL),
      _autoHeartbeat(true)
{
    _serverUrl = "http://" + _serverHost + ":" + String(_serverPort);
    _classification.valid = false;
}

void ITSClient::setDeviceInfo(const char* deviceId, const char* hardwareType) {
    _deviceId = String(deviceId);
    _hardwareType = String(hardwareType);
}

void ITSClient::setMetadata(const char* location, const char* firmwareVersion) {
    if (location) _location = String(location);
    if (firmwareVersion) _firmwareVersion = String(firmwareVersion);
}

void ITSClient::begin() {
    _state = ITS_STATE_CONNECTING;
    Serial.println("[ITS] Client initialized");
    Serial.print("[ITS] Server: ");
    Serial.println(_serverUrl);
}

bool ITSClient::registerDevice() {
    if (_deviceId.isEmpty() || _hardwareType.isEmpty()) {
        _setError("Device ID and hardware type required");
        return false;
    }

    Serial.println("[ITS] Registering device...");

    String payload = _buildRegistrationJson();
    String response;

    bool success = _httpPost("/api/devices/register", payload, response);

    if (success) {
        _isRegistered = true;
        _state = ITS_STATE_ACTIVE;
        _lastHeartbeat = millis();

        // Parse classification from response
        _parseClassification(response);

        Serial.println("[ITS] Registration successful");
        if (_classification.valid) {
            Serial.print("[ITS] Classified as: ");
            Serial.print(_classification.deviceType);
            Serial.print(" (");
            Serial.print(_classification.confidence * 100, 1);
            Serial.println("%)");
        }
    } else {
        _state = ITS_STATE_ERROR;
        Serial.println("[ITS] Registration failed");
    }

    return success;
}

bool ITSClient::sendHeartbeat() {
    if (!_isRegistered) {
        _setError("Device not registered");
        return false;
    }

    String endpoint = "/api/devices/" + _deviceId + "/heartbeat";
    String response;

    bool success = _httpPost(endpoint.c_str(), "{}", response);

    if (success) {
        _lastHeartbeat = millis();
        _state = ITS_STATE_ACTIVE;
        Serial.println("[ITS] Heartbeat sent");
    } else {
        Serial.println("[ITS] Heartbeat failed");
    }

    return success;
}

bool ITSClient::updateState(const char* state) {
    if (!_isRegistered) {
        _setError("Device not registered");
        return false;
    }

    String endpoint = "/api/devices/" + _deviceId + "/state";
    String payload = "{\"state\":\"" + String(state) + "\"}";
    String response;

    bool success = _httpPut(endpoint.c_str(), payload, response);

    if (success) {
        Serial.print("[ITS] State updated to: ");
        Serial.println(state);

        // Update local state
        if (strcmp(state, "active") == 0) _state = ITS_STATE_ACTIVE;
        else if (strcmp(state, "inactive") == 0) _state = ITS_STATE_INACTIVE;
        else if (strcmp(state, "error") == 0) _state = ITS_STATE_ERROR;
    }

    return success;
}

void ITSClient::loop() {
    // Auto heartbeat
    if (_autoHeartbeat && _isRegistered) {
        unsigned long now = millis();
        if (now - _lastHeartbeat >= _heartbeatInterval) {
            sendHeartbeat();
        }
    }
}

// ========== Private Methods ==========

String ITSClient::_buildRegistrationJson() {
    StaticJsonDocument<512> doc;

    doc["id"] = _deviceId;
    doc["ip"] = WiFi.localIP().toString();
    doc["hardware"] = _hardwareType;

    // Optional fields
    if (!_location.isEmpty()) {
        doc["location"] = _location;
    }
    if (!_firmwareVersion.isEmpty()) {
        doc["firmware_version"] = _firmwareVersion;
    }

    // Add memory info
    doc["memory_total"] = ESP.getHeapSize();
    doc["memory_free"] = ESP.getFreeHeap();
    doc["cpu_freq_mhz"] = ESP.getCpuFreqMHz();

    // Connection type
    doc["connection_type"] = "wifi";

    String output;
    serializeJson(doc, output);
    return output;
}

bool ITSClient::_parseClassification(const String& json) {
    StaticJsonDocument<1024> doc;
    DeserializationError error = deserializeJson(doc, json);

    if (error) {
        return false;
    }

    if (doc.containsKey("classification")) {
        JsonObject classification = doc["classification"];
        _classification.deviceType = classification["device_type"].as<String>();
        _classification.confidence = classification["confidence"].as<float>();
        _classification.suggestedRole = classification["suggested_role"].as<String>();
        _classification.valid = true;
        return true;
    }

    return false;
}

bool ITSClient::_httpPost(const char* endpoint, const String& payload, String& response) {
    HTTPClient http;
    String url = _serverUrl + String(endpoint);

    http.begin(url);
    http.addHeader("Content-Type", "application/json");

    int httpCode = http.POST(payload);

    if (httpCode > 0) {
        response = http.getString();

        if (httpCode == 200 || httpCode == 201) {
            http.end();
            return true;
        } else {
            _setError(("HTTP error: " + String(httpCode)).c_str());
        }
    } else {
        _setError(("Connection error: " + http.errorToString(httpCode)).c_str());
    }

    http.end();
    return false;
}

bool ITSClient::_httpGet(const char* endpoint, String& response) {
    HTTPClient http;
    String url = _serverUrl + String(endpoint);

    http.begin(url);

    int httpCode = http.GET();

    if (httpCode > 0) {
        response = http.getString();

        if (httpCode == 200) {
            http.end();
            return true;
        }
    }

    http.end();
    return false;
}

bool ITSClient::_httpPut(const char* endpoint, const String& payload, String& response) {
    HTTPClient http;
    String url = _serverUrl + String(endpoint);

    http.begin(url);
    http.addHeader("Content-Type", "application/json");

    int httpCode = http.PUT(payload);

    if (httpCode > 0) {
        response = http.getString();

        if (httpCode == 200) {
            http.end();
            return true;
        }
    }

    http.end();
    return false;
}

void ITSClient::_setError(const char* error) {
    _lastError = String(error);
    Serial.print("[ITS] Error: ");
    Serial.println(error);
}
