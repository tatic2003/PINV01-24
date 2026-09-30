# IoT-ITS Server Docker Image
# Project: PINV01-24 - FIUNA
#
# Build: docker build -t iot-its-server .
# Run:   docker run -p 5000:5000 iot-its-server

FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create data directories
RUN mkdir -p /app/data /app/models

# Environment variables
ENV ITS_HOST=0.0.0.0
ENV ITS_PORT=5000
ENV ITS_NODE_ID=docker-node-01
ENV ITS_LOG_LEVEL=INFO
ENV ITS_CLASSIFIER=rule_based
ENV ITS_ENABLE_DAG=true

# Expose port
EXPOSE 5000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:5000/health || exit 1

# Run the server
CMD ["python", "run_server.py"]
