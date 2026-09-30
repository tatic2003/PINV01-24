# Makefile for IoT-ITS Semantic Interoperability System
# Project: PINV01-24 - FIUNA

.PHONY: help install dev test lint run clean docker docker-up docker-down

# Default target
help:
	@echo "IoT-ITS Interoperability System"
	@echo "================================"
	@echo ""
	@echo "Usage: make [target]"
	@echo ""
	@echo "Targets:"
	@echo "  install     Install production dependencies"
	@echo "  dev         Install development dependencies"
	@echo "  test        Run all tests"
	@echo "  test-cov    Run tests with coverage"
	@echo "  lint        Run code linting"
	@echo "  format      Format code with black"
	@echo "  run         Start the server"
	@echo "  run-debug   Start server in debug mode"
	@echo "  cli         Show CLI help"
	@echo "  evaluate    Generate learning curves and confusion matrices"
	@echo "  clean       Clean cache and build files"
	@echo "  docker      Build Docker image"
	@echo "  docker-up   Start Docker containers"
	@echo "  docker-down Stop Docker containers"
	@echo "  docker-logs View Docker logs"
	@echo ""

# ============================================================
# Installation
# ============================================================

install:
	pip install -r requirements.txt

dev: install
	pip install pytest-cov black flake8 mypy

# ============================================================
# Testing
# ============================================================

test:
	pytest tests/ -v

test-cov:
	pytest tests/ -v --cov=. --cov-report=html --cov-report=term

test-semantic:
	pytest tests/test_semantic_layer.py -v

test-pnp:
	pytest tests/test_pnp_ai.py -v

test-dag:
	pytest tests/test_blockdag.py -v

test-api:
	pytest tests/test_api_integration.py -v

test-mqtt:
	pytest tests/test_mqtt.py -v

# ============================================================
# Code Quality
# ============================================================

lint:
	flake8 --max-line-length=100 --ignore=E501,W503 \
		semantic_layer/ pnp_ai/ edge_agents/ blockdag/ mqtt/ server/

format:
	black --line-length=100 \
		semantic_layer/ pnp_ai/ edge_agents/ blockdag/ mqtt/ server/ tests/

typecheck:
	mypy --ignore-missing-imports \
		semantic_layer/ pnp_ai/ edge_agents/ blockdag/ mqtt/ server/

# ============================================================
# Running
# ============================================================

run:
	python run_server.py

run-debug:
	python run_server.py --debug

run-port:
	python run_server.py --port $(PORT)

cli:
	python cli.py --help

status:
	python cli.py status

devices:
	python cli.py devices list

# ============================================================
# Docker
# ============================================================

docker:
	docker build -t iot-its-server .

docker-up:
	docker compose up -d

docker-down:
	docker compose down

docker-logs:
	docker compose logs -f

docker-restart:
	docker compose down && docker compose up -d

docker-clean:
	docker compose down -v --rmi local

# ============================================================
# Cleanup
# ============================================================

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	find . -type f -name "*.pyo" -delete 2>/dev/null || true
	find . -type f -name ".coverage" -delete 2>/dev/null || true
	rm -rf htmlcov/ 2>/dev/null || true
	rm -rf .mypy_cache/ 2>/dev/null || true

clean-all: clean docker-clean

# ============================================================
# Development Helpers
# ============================================================

# Generate synthetic dataset
generate-data:
	python -c "from pnp_ai.dataset_generator import SyntheticDataGenerator; \
		g = SyntheticDataGenerator(); \
		df = g.generate(1000); \
		df.to_csv('data/synthetic_devices.csv', index=False); \
		print('Generated 1000 samples to data/synthetic_devices.csv')"

# Train classifier
train:
	python -c "from pnp_ai.device_classifier import train_classifier_from_synthetic_data; \
		classifier, metrics = train_classifier_from_synthetic_data(1000, 'random_forest'); \
		print('Accuracy:', metrics.get('accuracy', 'N/A'))"

# Generate model evaluation plots (learning curves and confusion matrices)
evaluate:
	python generate_model_evaluation.py --samples 1000 --output-dir ./evaluation_plots

evaluate-detailed:
	python generate_model_evaluation.py --samples 2000 --output-dir ./evaluation_plots

# Generate BlockDAG performance evaluation plots
evaluate-dag:
	python generate_dag_evaluation.py --nodes 500 --syncs 10 --output-dir ./dag_evaluation

# Export RDF
export-rdf:
	python cli.py export rdf > output.ttl
	@echo "Exported to output.ttl"

# Validate ontology
validate:
	python -c "from semantic_layer import SemanticDeviceManager; \
		m = SemanticDeviceManager(); \
		r = m.validate_graph(); \
		print('Valid:', r.conforms)"

# ============================================================
# Project Info
# ============================================================

info:
	@echo "Project: PINV01-24"
	@echo "Institution: FIUNA - Universidad Nacional de Asunción"
	@echo ""
	@echo "Structure:"
	@echo "  semantic_layer/  - RDF/SHACL management"
	@echo "  pnp_ai/          - Device classification"
	@echo "  edge_agents/     - Distributed agents"
	@echo "  blockdag/        - Event synchronization"
	@echo "  mqtt/            - MQTT support"
	@echo "  server/          - REST API"
	@echo "  firmware/esp32/  - ESP32 library"
	@echo ""
	@python --version
	@pip --version

version:
	@echo "1.0.0"
