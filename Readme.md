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
- **MQTT Support** - Lightweight IoT messaging protocol
- **ESP32 Firmware** - Arduino/C++ library for microcontrollers

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         Central Server                           │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐ │
│  │  REST API   │  │ MQTT Broker │  │  Semantic Layer (RDF)   │ │
│  │  (Flask)    │  │ (Mosquitto) │  │  PnP-AI Classifier      │ │
│  └──────┬──────┘  └──────┬──────┘  │  BlockDAG Storage       │ │
│         │                │         └─────────────────────────┘ │
└─────────┼────────────────┼─────────────────────────────────────┘
          │ HTTP/REST      │ MQTT
          │                │
┌─────────┼────────────────┼─────────────────────────────────────┐
│         │   Edge Layer   │                                      │
│  ┌──────┴──────┐  ┌──────┴──────┐  ┌────────────────────────┐ │
│  │ Raspberry Pi│  │ Jetson Nano │  │   ESP32 Devices        │ │
│  │ Edge Agent  │  │ Edge Agent  │  │   (HTTP/MQTT Client)   │ │
│  │ + BlockDAG  │  │ + BlockDAG  │  │                        │ │
│  └─────────────┘  └─────────────┘  └────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

---

## Quick Start

### Prerequisites

- Python 3.9+
- pip
- (Optional) Docker & Docker Compose
- (Optional) Arduino IDE for ESP32

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
```

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

### MQTT Topics

```
its/devices/register           # Device registration
its/devices/{id}/heartbeat     # Heartbeat
its/devices/{id}/state         # State changes
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
3. Update WiFi credentials and server IP
4. Flash to ESP32

Example:
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

- **Edge Nodes:** Raspberry Pi 4/5, NVIDIA Jetson Nano
- **Microcontrollers:** ESP32-WROOM32, ESP32-CAM
- **Gateways:** Industrial IoT Gateways

---
