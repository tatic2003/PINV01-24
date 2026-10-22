# Interoperability of heterogeneous devices in ITS: semantic validation, PnP-AI and DAG-based synchronization on edge hardware

**Project:** PINV01-24, *Interoperabilidad de Equipos y Dispositivos con capacidad Plug and Play aplicado a Sistemas de Transportes Inteligentes: Un paso hacia las Smart Cities*
**Institution:** Centro de Innovación Tecnológica (CITEC), Facultad de Ingeniería, Universidad Nacional de Asunción, Paraguay
**License:** MIT (see `LICENSE`)

This repository holds the system, the experiments and the measured data of the article *Interoperability of Heterogeneous Devices in Intelligent Transportation Systems: Semantic Validation, PnP-AI, and DAG-Based Synchronization on Edge Hardware* (T. Caballero and D. O. Gregor). Every number in the article comes from a script in this repository and from raw data committed here.

## What the system does

Devices announce themselves over MQTT. On each edge node a bridge turns the MQTT events into REST calls to a Flask server, which:

1. rejects malformed announcements with a lightweight check,
2. classifies the device into one of six hardware classes with a random forest trained on a calibrated catalogue of 114 commercial boards (PnP-AI),
3. validates the RDF description of the device with SHACL shapes, using the OWL subclass axioms of the ontology,
4. records the event in a hash-chained BlockDAG whose nodes are signed with Ed25519.

Next to each server, a synchronization agent pulls new DAG nodes from its peers and merges them independently of the order in which they arrive. Departures are detected through the MQTT Last Will.

## Repository layout

```
server/, run_server.py       Flask server (registration, classification, SHACL, DAG)
mqtt_http_bridge.py          MQTT -> HTTP bridge; writes the event and onboarding CSVs
semantic_layer/              RDF store, SHACL validator, OWL/XML loader
ontology/                    ITS base ontology, iot_pnp.owl extension, SHACL shapes
pnp_ai/                      features, classifier, catalogue-based announcement generator
blockdag/                    DAG, Ed25519 signing, storage
devices/                     agents: Linux boards, GNSS receiver, serial (Arduino Uno)
firmware/                    ESP32 sketches (Wi-Fi, Ethernet/PoE) and Arduino Uno sketch
datasets/catalog/            catalogue of 114 boards with the measured calibration
datasets/real/               announcements captured from the physical boards
baseline/                    pipeline benchmarks, classifier evaluation, GNSS ablation
experiments/                 experiment runners and analysis scripts
experiments/results/         raw data and analysis outputs of every experiment
figures/                     make_figures.py, its configuration and its input data
tests/                       automated tests
```

`edge_agents/`, `examples/`, `cli.py`, `mqtt/` and `generate_*.py` are not used in the experiments of the article.

## Installation and tests

Python 3.9 or later.

```
pip install -r requirements.txt
python -m pytest -q
```

## Running the pipeline

The configuration evaluated in the article is the default one: random forest, `resource` features, training on the calibrated catalogue (2000 announcements) and SHACL on every registration.

Server, on each edge node:

```
python run_server.py
```

To sign DAG nodes and accept only nodes from known servers, add `--dag-key-file`, `--dag-trusted-keys` and `--dag-require-signatures`. Keep the key file outside the repository.

Bridge, on the same node:

```
python mqtt_http_bridge.py --mqtt-host localhost --http-host localhost --onboarding-csv onboarding.csv --session <label>
```

Under heavy load use `--keepalive 600`; the reason is in `python mqtt_http_bridge.py --help`.

Devices:

- ESP32 boards: sketches in `firmware/esp32/examples/mqtt_device/`. Copy `wifi_secrets.example.h` to `wifi_secrets.h` next to the sketch and fill in the network. `wifi_secrets.h` is ignored by git.
- Linux boards: `python3 devices/linux_agent.py --mqtt-host <broker>`
- GNSS receiver on the Raspberry Pi 5: `devices/gnss_agent.py`
- Arduino Uno over USB: flash `firmware/arduino/mqtt_device_uno_serial/` and run `python devices/serial_agent.py` on the host.
- DAG replication: `python3 experiments/dag_sync.py --local http://localhost:5000 --peer http://<peer>:5000`

Each script documents its options in its header and in `--help`.

## Trying the system with Docker

`docker-compose.yml` starts two edge nodes, as on the test bench: each one with its own Mosquitto broker, server (configuration of the article, DAG nodes signed with Ed25519 and signatures required), MQTT bridge and DAG synchronization agent. A first service creates the keys of both nodes and their trust store in a Docker volume.

```
docker compose up -d --build
docker compose run --rm demo
```

The second command makes 10 emulated devices from the calibrated catalogue announce themselves, split between the two brokers. Each node registers its own devices and the agents replicate the DAG, so after a few seconds both servers report the same digest and tips:

```
curl http://localhost:5000/api/dag/status
curl http://localhost:5001/api/dag/status
```

The CSVs of the bridges, the agents and the generator are written to `docker/out/`. `docker compose down` stops everything, and `docker compose down -v` also deletes the keys.

The containers are for trying the system. The timings in the article were measured on the Raspberry Pi 5 and the Orange Pi Zero without containers, and a run in Docker does not reproduce them.

## Reproducing the article

### Figures

```
python figures/make_figures.py
```

The command regenerates every data figure into `figures/out/`, together with a manifest of the SHA-256 of every input. `figures/README.md` lists the figures and their data.

### Tables and reported values

| Article | Script | Data |
|---|---|---|
| Calibration of the catalogue | `datasets/catalog/build_catalogo.py` (validator) | `datasets/catalog/catalogo_placas.csv` (measured columns) |
| Synthetic to real devices | `experiments/eval_synth_to_real.py` | `experiments/results/synth_to_real/` |
| Boards never seen in training | `experiments/eval_unseen_boards.py` | `experiments/results/unseen_boards/` |
| GNSS ablation | `baseline/run_gnss_ablation.py` | `baseline/results_gnss_ablation/` |
| GNSS receiver classification (10 runs) | `devices/gnss_agent.py` and the bridge | `experiments/results/gnss_classification/` |
| GNSS announcement validation | `experiments/run_gnss_validation.py` | `experiments/results/gnss_validation/` |
| Onboarding stages and clock bounds | `experiments/analyze_onboarding.py` | raw: `experiments/results/onboarding_*/`; analysis: `experiments/results/onboarding_analysis/` |
| Last Will of the wired boards | `experiments/run_lwt_trials.py` | `figures/data/lwt_ethernet/` |
| Load on the Raspberry Pi 5 and the Orange Pi Zero | `experiments/analyze_load.py` | raw: `experiments/results/load_pi5/`, `load_opi/`; analysis: `experiments/results/load_analysis/` |
| Emulated vs physical devices | `experiments/analyze_load.py --validate` | `experiments/results/load_pi5val/` and, with the `performance` governor, `load_pi5perf/` |
| Centralized vs distributed placement | `experiments/analyze_placement.py` | `experiments/results/placement/` |
| Convergence after partition | `experiments/run_partition.py`, `experiments/analyze_convergence.py` | `experiments/results/convergence/` |
| Pipeline stage costs (supplementary) | `baseline/run_benchmark.py`, `baseline/run_distributed_benchmark.py` | `baseline/results_pi5_catalog/` |
| Garbage-collector diagnostic (supplementary) | `baseline/diagnose_gc.py` | `baseline/results_pi5_catalog/gc_*` |
| Invalid announcements (supplementary) | `experiments/run_invalid_events.py` | `experiments/results/invalid_events/` |
| Classifier on the synthetic test set (supplementary) | `baseline/plot_classifier_metrics.py` | `baseline/results_classifier_hw_pc/` |
| GPS time to first fix and clock error (supplementary) | `experiments/ttff_gps.py`, chrony logs | `figures/data/ttff/`, `figures/data/chrony/` |

Each experiment folder keeps, next to the raw CSVs, the conditions of the node (`conditions.csv`: load average and, where the board exposes them, temperature and throttling) and, when two clocks are involved, the `chronyc tracking` readings taken at the start and at the end.

### Where the data live

- `experiments/results/` holds the raw data and analysis outputs of each experiment.
- `figures/data/` holds the inputs of `make_figures.py`. Some are copies of files in `experiments/results/`, and the time to first fix, chrony and Last-Will series exist only there.
- `baseline/results_*` holds the outputs of the benchmarks and the classifier evaluations.

### Privacy

The NMEA logs have latitude and longitude removed. The coordinates in the GNSS validation and classification files are example coordinates in the centre of Asunción, not the position of the receiver. The only addresses in the chrony logs are those of public NTP servers. No Wi-Fi credentials or DAG keys are stored in the repository.

## Citation

If you use this code or data, please cite the article and the archived release of this repository (Zenodo DOI to be added).

## Funding

Project PINV01-24 is co-funded by the Consejo Nacional de Ciencia y Tecnología (CONACYT), Paraguay, with the support of the Fondo para la Excelencia de la Educación y la Investigación (FEEI).
