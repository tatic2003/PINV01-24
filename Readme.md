# IoT-ITS Semantic Interoperability System
 
**Project:** PINV01-24
**Institution:** FIUNA - Universidad Nacional de Asunción, Paraguay
**Version:** 1.0.0
 
---
 
## Overview
 
A semantic interoperability system for IoT devices in Intelligent Transportation Systems (ITS). This project implements Plug-and-Play AI (PnP-AI) for automatic device classification, decentralized edge computing, and BlockDAG-based event synchronization.
 
### Key Features
 
- **Semantic Interoperability** - OWL/RDF/SHACL-based device representation
- **PnP-AI Classification** - Automatic device type detection using rule-based and ML algorithms
- **Edge Agents** - Distributed agents for discovery, classification, and synchronization
- **BlockDAG** - Non-linear ledger for decentralized event logging
- **REST API** - Flask-based HTTP interface
- **MQTT Support** - Lightweight IoT messaging protocol via MQTT→HTTP bridge
- **ESP32 Firmware** - Arduino/C++ library for microcontrollers
---
 
## Architecture
 
```
┌─────────────────────────────────────────────────────────────────┐
│                         Central Server                          │
│  ┌─────────────┐   ┌──────────────┐   ┌──────────────────────┐  │
│  │ MQTT Broker │──▶│  MQTT→HTTP   │──▶│  REST API (Flask)    │  │
│  │ (Mosquitto) │   │    Bridge    │   │  Semantic Layer (RDF)│  │
│  └──────▲──────┘   └──────────────┘   │  PnP-AI Classifier   │  │
│         │                             │  BlockDAG Storage    │  │
│         │                             └──────────▲───────────┘  │
└─────────┼────────────────────────────────────────┼──────────────┘
          │ MQTT                                   │ HTTP/REST
          │                                        │
┌─────────┼────────────────────────────────────────┼──────────────┐
│         │              Edge Layer                │              │
│  ┌──────┴──────┐  ┌─────────────┐  ┌─────────────┴───────────┐  │
│  │ ESP32 / SBC │  │ Raspberry Pi│  │   Edge Agents           │  │
│  │ MQTT Client │  │ Orange Pi   │  │   (HTTP/REST Client)    │  │
│  │ + LWT       │  │ + BlockDAG  │  │                         │  │
│  └─────────────┘  └─────────────┘  └─────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```
 
> The Flask server does **not** subscribe to MQTT. Devices that speak MQTT are
> served by `mqtt_http_bridge.py`, which subscribes to the broker and translates
> each message into the corresponding REST call. Departure detection relies on
> the MQTT Last Will and Testament published on the device's `state` topic.
 
---
 
## Quick Start
 
### Prerequisites
 
- Python 3.9+
- pip
- (Optional) Docker & Docker Compose
- (Optional) Arduino IDE for ESP32
- (Optional) Mosquitto broker, for MQTT devices
### Installation
 
```bash
# Clone or extract the project
cd iot-its-interoperability
 
# Install dependencies
make install
 
# Or manually:
pip install -r requirements.txt
 
# Verify installation
python -c "from semantic_layer import SemanticDeviceManager; print('OK')"
```
 
### Run Server
 
```bash
# Using Make
make run
 
# Or directly
python run_server.py
 
# With options
python run_server.py --port 8080 --debug
 
# Choosing classifier and feature set
python run_server.py --classifier random_forest --feature-set resource
```
 
Feature sets:
 
| Value | Features used |
|-------|---------------|
| `resource` | Only quantitative resource characteristics (memory, CPU, cores, link type) |
| `resource+name` | The above plus hardware-model-name flags (`has_esp32`, `has_raspberry`, ...) |
 
`resource+name` reaches higher accuracy, but the name flags encode the same
rules as the `rule_based` baseline, which makes a comparison between the two
approaches circular. Use `resource` when the point is to evaluate the ML
classifier on its own merits.
 
### Run MQTT Bridge
 
Required for any device that announces itself over MQTT:
 
```bash
python mqtt_http_bridge.py --broker localhost --server http://localhost:5000
```
 
It writes one CSV row per event (`register`, `heartbeat`, `state`, `data`,
`lwt_departure`) for later analysis.
 
### Run Tests
 
```bash
# All tests
make test
 
# With coverage
make test-cov
```
 
---
 
## Project Structure
 
```
iot-its-interoperability/
├── semantic_layer/          # RDF/SHACL device management
│   ├── rdf_store.py         # RDF graph operations
│   ├── validator.py         # SHACL validation
│   └── device_manager.py    # High-level API
├── pnp_ai/                  # Plug-and-Play AI
│   ├── features.py          # Feature extraction (single source of truth)
│   ├── device_classifier.py # Classification engine
│   └── dataset_generator/   # Synthetic data generation
├── edge_agents/             # Distributed agents
│   ├── discovery_agent.py   # Device discovery
│   ├── classification_agent.py
│   └── sync_agent.py        # BlockDAG sync
├── blockdag/                # Decentralized ledger
│   ├── dag.py               # DAG implementation
│   └── storage.py           # Persistence backends
├── mqtt/                    # MQTT support
│   ├── broker.py            # Configuration
│   ├── client.py            # Async client
│   └── handlers.py          # Message handlers
├── server/                  # REST API
│   ├── app.py               # Flask application
│   └── config.py            # Configuration
├── baseline/                # Comparative evaluation
│   ├── central_baseline.py
│   └── run_distributed_benchmark.py
├── ontology/
│   ├── base/                # ITS.owl (provided)
│   ├── extensions/          # iot_pnp.owl
│   └── shapes/              # SHACL validation shapes
├── firmware/esp32/          # Arduino library
│   ├── its_client.h/.cpp    # C++ library
│   └── examples/            # Example sketches
├── tests/                   # Unit & integration tests
├── config/                  # YAML configuration
├── cli.py                   # CLI tool
├── run_server.py            # Server entry point
├── mqtt_http_bridge.py      # MQTT→HTTP bridge (required for MQTT devices)
├── its_linux_agent.py       # Agent for Linux SBCs (Orange Pi, Raspberry Pi)
├── Dockerfile
├── docker-compose.yml
├── Makefile
└── requirements.txt
```
 
---
 
## Usage
 
### CLI Tool
 
```bash
# Server status
python cli.py status
 
# List devices
python cli.py devices list
 
# Register device
python cli.py devices register '{"id":"esp32-01","ip":"192.168.1.100","hardware":"ESP32"}'
 
# Classify device
python cli.py classify '{"hardware_type":"ESP32","memory_total":520000}'
 
# Export RDF
python cli.py export rdf
```
 
### REST API
 
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check |
| GET | `/api/status` | Server status |
| POST | `/api/devices/register` | Register device |
| GET | `/api/devices` | List devices |
| GET | `/api/devices/{id}` | Get device |
| PUT | `/api/devices/{id}/state` | Update state |
| DELETE | `/api/devices/{id}` | Remove device |
| POST | `/api/classify` | Classify device |
| GET | `/api/semantic/export` | Export RDF |
| GET | `/api/semantic/validate` | SHACL validation |
| GET | `/api/dag/status` | DAG status |
| GET | `/api/dag/events` | DAG events |
 
The registration response includes the classifier decision together with
`low_confidence` and `confidence_threshold`. When confidence falls below the
threshold, the device is persisted under the generic `IoTDevice` class instead
of an asserted specific type, and a WARNING is logged.
 
### MQTT Topics
 
```
its/devices/register           # Device registration
its/devices/{id}/heartbeat     # Heartbeat
its/devices/{id}/state         # State changes (carries the Last Will payload)
its/devices/{id}/data          # Sensor data
its/devices/{id}/command       # Commands
its/sync/broadcast             # DAG synchronization
```
 
---
 
## Docker Deployment
 
```bash
# Start all services
make docker-up
 
# Or manually
docker-compose up -d
 
# View logs
docker-compose logs -f
 
# Stop
docker-compose down
```
 
---
 
## ESP32 Integration
 
1. Copy `firmware/esp32/its_client.h` and `its_client.cpp` to your Arduino project
2. Install dependencies: `ArduinoJson`, `PubSubClient` (for MQTT)
3. Update WiFi credentials, server IP and device identity
4. Flash to ESP32
Configuration in `firmware/esp32/examples/mqtt_device/mqtt_device.ino`:
 
```cpp
const char* WIFI_SSID     = "...";
const char* WIFI_PASSWORD = "...";
const char* MQTT_BROKER   = "192.168.1.1";
const char* DEVICE_ID     = "esp32-wroom-01";
const char* HARDWARE_TYPE = "ESP32-WROOM32";
 
// Physical link, independent of the application protocol (MQTT).
// Use "wifi" or "ethernet" — do not infer it from the protocol.
const char* LINK_TYPE = "wifi";
 
// Bounds departure detection: the broker declares the device dead
// after 1.5x this value.
const uint16_t MQTT_KEEPALIVE_SECONDS = 15;
```
 
Minimal HTTP example:
 
```cpp
#include "its_client.h"
 
ITSClient its("192.168.1.1", 5000);
 
void setup() {
    WiFi.begin(ssid, password);
    its.begin("esp32-01", "ESP32-WROOM32");
    its.registerDevice();
}
 
void loop() {
    its.sendHeartbeat();
    delay(30000);
}
```
 
---
 
## Make Commands
 
```bash
make help          # Show all commands
make install       # Install dependencies
make test          # Run tests
make test-cov      # Tests with coverage
make run           # Start server
make docker-up     # Start Docker containers
make clean         # Clean cache files
make lint          # Run linting
make format        # Format code
```
 
---
 
## Technology Stack
 
| Component | Technology |
|-----------|------------|
| Language | Python 3.9+ |
| Semantic Web | rdflib, pyshacl, OWL |
| Machine Learning | scikit-learn |
| Web Framework | Flask |
| MQTT | paho-mqtt, Mosquitto |
| Async | asyncio, aiohttp |
| Firmware | Arduino/C++ |
| Container | Docker |
| Testing | pytest |
 
---
 
## Target Hardware
 
- **Edge Nodes:** Raspberry Pi 4/5, Orange Pi Zero LTS, NVIDIA Jetson Nano
- **Microcontrollers:** ESP32-WROOM32, ESP32-C3-Zero, ESP32-CAM
- **Gateways:** Industrial IoT Gateways
