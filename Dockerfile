# Image of the IoT-ITS pipeline (server, MQTT bridge, DAG synchronization
# agent, load generator). docker-compose.yml starts two edge nodes with it.
#
# The containers are for trying the system, not for reproducing the timings
# of the article, which were measured on the boards without containers.
#
# Build: docker build -t pinv01-24 .

ARG BASE_IMAGE=python:3.11-slim
FROM ${BASE_IMAGE}

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1
EXPOSE 5000

# Server with the configuration evaluated in the article (random forest,
# resource features, calibrated catalogue, SHACL on every registration).
CMD ["python", "run_server.py"]
