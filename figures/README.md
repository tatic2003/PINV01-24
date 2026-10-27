# Figures of the article

Every data figure of the article is regenerated from the measured data with one command, run from the repository root:

```
python figures/make_figures.py            # all figures
python figures/make_figures.py --list     # figures and the data each one reads
python figures/make_figures.py fig_lwt_detection   # only one
```

The figures are written to `figures/out/` as PDF (for the article) and PNG. `figures/out/MANIFEST.csv` lists, for each figure, every data file it read and its SHA-256. Values that are computed here, rather than by an experiment's own analysis, are also written next to the figure (`<figure>_data.csv`).

No value shown in a figure is written in the code. The only fixed values are in `figures/config.yml`: they select data (time windows, NTP sources) or describe the experiment (keepalive, start types), each with a comment. A figure whose data are not in the repository yet is skipped with a message (`--strict` makes that an error).

## Figures and their data

| Figure | Data | Produced by |
|---|---|---|
| `fig_baseline_stages`, `fig_baseline_latency` | `baseline/results_pi5_catalog/*_events.csv` | `baseline/run_benchmark.py`, `baseline/run_distributed_benchmark.py`; drawn by `baseline/plot_centralized_vs_descentralized_fig.py` |
| `fig_gnss_ttff` | `data/ttff/` | `experiments/ttff_gps.py` |
| `fig_clock_error`, `fig_nmea_residual` | `data/chrony/` | chrony (`log statistics tracking`) on the Raspberry Pi 5 |
| `fig_gnss_device` | `data/gnss_device/` | `experiments/run_gnss_lwt.py`, `experiments/run_gnss_validation.py` |
| `fig_lwt_detection` | `data/lwt_ethernet/` | `experiments/run_lwt_trials.py` and the CSV of `mqtt_http_bridge.py` |
| `fig_onboarding_stages` | `data/onboarding/` | `mqtt_http_bridge.py --onboarding-csv`, `devices/serial_agent.py`, `experiments/clock_probe.py`; drawn by `experiments/analyze_onboarding.py` |
| `fig_load_burst`, `fig_load_ramp` | `data/load/` | `experiments/load_generator.py`, `experiments/resource_monitor.py`, bridge; drawn by `experiments/analyze_load.py` |
| `fig_convergence` | `data/convergence/` | `experiments/run_partition.py`, `experiments/dag_sync.py`; drawn by `experiments/analyze_convergence.py` |
| `fig_placement` | `data/placement/` (three blocks) | `experiments/load_generator.py` (several `--host`), `experiments/resource_monitor.py`, `experiments/dag_sync.py`; drawn by `experiments/analyze_placement.py` |

## The data files

### `data/ttff/` — time to first fix of the GPS HAT

- `ttff_hot.csv`, `ttff_cold_command.csv`, `ttff_full_cold.csv`: one row per run (3 per start type), written by `ttff_gps.py`. Columns (Spanish names, as the script writes them): `corrida` run, `tipo` start type (`caliente` hot, `frio` cold by command, `completo` full cold), `antena_etiqueta` antenna, `pgtop` antenna status code, `inicio_utc` time the restart command was sent, `ttff_s` time to first fix, `calidad_gga` GGA fix quality, `satelites`, `hdop`, `nota`.
- `ttff_*_nmea.txt`: NMEA sentences of the same runs, each prefixed with its host arrival time (UTC). Latitude and longitude were removed by `ttff_gps.py` before writing. `make_figures.py` recomputes every TTFF from this log (first RMC with status A after a GGA with quality ≥ 1) and stops if it differs from the CSV by more than 0.15 s.
- `power_cycle_battery_nmea.txt`: NMEA logged from the boot of the Pi after a power cut with the backup cell. It supports the power-cycle row of the TTFF table; it has no start time, so it is not in the figure.

### `data/chrony/` — clock of the Raspberry Pi 5

- `statistics.log`, `tracking.log`: chrony logs of the session with the two configurations: the clock disciplined by four NTP servers (PPS and NMEA only monitored) and, after a restart of chrony, the clock disciplined by the PPS. The windows used are in `config.yml`. The only addresses in the logs are those of public NTP servers.

### `data/gnss_device/` — GNSS receiver as an ITS device

- `gnss_lwt.csv`: 10 freezes of the GNSS agent (`SIGSTOP`) with keepalive 15 s: time from the last packet and from the freeze to the Last Will, and the state recorded by the server.
- `gnss_validacion.csv`: 12 cases × 30 runs of valid and invalid GNSS announcements: expected result, HTTP status, decision of the server and of SHACL, registration time and SHACL time. The `lat_not_numeric` case is rejected before SHACL and has no SHACL time.

### `data/lwt_ethernet/` — Last Will of the wired boards

- `lwt_trials_<board>_k<K>.csv`: cable cuts of the Olimex ESP32-PoE and the M5Stack PoE CAM-W with keepalive K = 15 s and 5 s (10 cuts each): last heartbeat, cut, Last Will, detection, link back, recovery. The board and K are given by the file name only.
- `bridge_olimex.csv`, `bridge_poecam.csv`: everything the bridge received from each board during both series (registrations, heartbeats, data, state, Last Will). `bridge_poecam.csv` also has one leftover row of the Olimex at its start; the figure filters by device.
- The time from the last device packet to the cut is computed as the latest of the last heartbeat or data message received before the Last Will and the last PINGREQ, which does not reach the bridge and is estimated as the connection time plus multiples of K.

### `data/placement/` — centralized vs distributed placement

- Each placement was run three times, once in each of three blocks, in a Latin-square order: block 1 (centralized, even split, capacity split), block 2 (even, capacity, centralized) and block 3 (capacity, centralized, even). The Orange Pi Zero was at 192.168.0.15 in all three blocks. The files of each run are in `block<N>/<placement>/`, copied from `experiments/results/placement/`.
- Placements: `central` (every announcement to the Raspberry Pi 5), `dist50` (50/50 split between the two nodes) and `distcap` (89/11 split).
- `<placement>/generator_<placement>.csv`: one row per announcement, written by `load_generator.py`, with the level (offered rate), the broker (`host`), the publication time and the time of the broker's acknowledgement.
- `<placement>/onboarding_<node>.csv`: one row per registration on node `pi5` or `opi`, written by `mqtt_http_bridge.py --onboarding-csv` (arrival at the bridge, HTTP status and the stage times returned by the server).
- `<placement>/resources_<node>.csv`: CPU, memory, temperature and CPU-0 frequency of the node, and CPU of its server, bridge, broker and synchronization agent, sampled every 0.5 s by `resource_monitor.py`.
- In `config.yml`, each run is named `<placement>:<block>`. The three blocks are aggregated by `analyze_placement.py`: each run weighs the same, and the figure shows the mean with the minimum and maximum of the three runs.
