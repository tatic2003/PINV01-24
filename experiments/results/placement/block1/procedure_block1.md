# Centralized vs. distributed placement: procedure of block 1 (21 October 2026)

This file documents how block 1 of the placement test was run. Its data are in `experiments/results/placement/central/`, `dist50/` and `distcap/` and are part of release v1.0.0. Blocks 2 and 3 were run later with a stricter procedure (`procedimiento_placement_bloques.md`), and their data are in `placement/block2/` and `placement/block3/`.

## Origin of these commands

The commands below are those of the step list followed on the first experiment of the blocks, and the author executed them exactly as written. They do not come from the shell history of the terminals, which was not kept. This file therefore describes the procedure that was followed and is not an automatic record of what was executed.

## Design

- Three configurations, one run each, in this order: `central`, `dist50`, `distcap`.
- Ramp of 2, 5, 10, 20 and 40 announcements per second, 20 s per level and 150 s of pause between levels (1540 announcements in total).
- `central`: everything to the Raspberry Pi 5. `dist50`: 50/50 split between the Pi 5 and the Orange Pi Zero. `distcap`: 89/11 split.
- Addresses: Pi 5 at 192.168.0.21 and Orange Pi Zero at 192.168.0.13.
- Terminals: 1 server, 2 bridge, 3 `dag_sync` agent, 4 monitor and 5 generator and one-off commands.
- Generator labels: `central`, `dist50`, `distcap`.

## Differences from blocks 2 and 3

| | Block 1 | Blocks 2 and 3 |
|---|---|---|
| DAG at the start of each run | Empty (server restarted; the DAG is kept in memory) | Empty (same; the `rm -f dag_storage/<node>.json` step had no effect, the directory is empty) |
| Mosquitto restart | Not among the steps | Yes, on both boards |
| Processes | Server, bridge, monitor and agent were stopped and relaunched | Same |
| Orange Pi address | 192.168.0.13 | 192.168.0.15 |
| Generator label | `dist50` | `dist50_b2`, `dist50_b3` |
| Shell history of each terminal | Not kept | Saved at the end of each run |

## Verified from the data

- Split of announcements: 770 and 770 in `dist50`, 1371 and 169 in `distcap`.
- In the first `dag_sync` round of `dist50` and `distcap`, each DAG held only the two genesis nodes, that is, both DAGs were empty at the start.
- About 8 minutes elapsed between the last registration of one configuration and the first announcement of the next.
- The Orange Pi agent was launched with `--local http://localhost:5000 --peer http://192.168.0.21:5000`.

## Not confirmed

- Nothing pending. The server never writes its DAG to disk (no `save` call in the code; `dag_storage/` is empty and unchanged since 5 October), so every server start gives an empty DAG, including the Pi 5 before `central`.

## Commands

### Run 1: centralized (Pi 5 only)

Step 1. Pi 5, terminal 5:

```bash
mkdir -p experiments/results/placement/central
```

```bash
cond before experiments/results/placement/central/conditions.csv
```

Step 2. Pi 5, terminal 1 (server):

```bash
python run_server.py --classifier random_forest --feature-set resource --training-source catalog --training-samples 2000 --node-id pi5 --dag-key-file ~/dag_signing_key.hex --dag-trusted-keys ~/trusted_keys.json --dag-require-signatures
```

Step 3. Pi 5, terminal 2 (bridge):

```bash
python mqtt_http_bridge.py --keepalive 600 --session placement --onboarding-csv experiments/results/placement/central/onboarding_pi5.csv --csv experiments/results/placement/central/eventos_bridge_pi5.csv
```

Step 4. Pi 5, terminal 4 (monitor):

```bash
python experiments/resource_monitor.py --node pi5 --csv experiments/results/placement/central/resources_pi5.csv
```

Step 5. Pi 5, terminal 5 (generator, about 15 minutes):

```bash
python experiments/load_generator.py --host localhost --label central --mode ramp --rates 2 5 10 20 40 --step-s 20 --settle 150 --csv experiments/results/placement/central/generator_central.csv
```

Step 6. Closing: Ctrl+C in the monitor (terminal 4), the bridge (terminal 2) and the server (terminal 1). Then, Pi 5, terminal 5:

```bash
cond after experiments/results/placement/central/conditions.csv
```

### Run 2: distributed 50/50

Step 7. Folders, conditions and chrony. Pi 5, terminal 5:

```bash
mkdir -p experiments/results/placement/dist50
```

```bash
cond before experiments/results/placement/dist50/conditions.csv
```

```bash
chronyc tracking > experiments/results/placement/dist50/chrony_inicio_pi5.txt
```

Orange Pi, terminal 5:

```bash
mkdir -p /root/PINV01-24/resultados/placement/dist50
```

```bash
chronyc tracking > /root/PINV01-24/resultados/placement/dist50/chrony_inicio_opi.txt
```

Step 8. Pi 5, terminal 1 (server):

```bash
python run_server.py --classifier random_forest --feature-set resource --training-source catalog --training-samples 2000 --node-id pi5 --dag-key-file ~/dag_signing_key.hex --dag-trusted-keys ~/trusted_keys.json --dag-require-signatures
```

Step 9. Orange Pi, terminal 1 (server; about 12 s were allowed for start-up):

```bash
python run_server.py --classifier random_forest --feature-set resource --training-source catalog --training-samples 2000 --node-id opi --dag-key-file /root/dag_signing_key.hex --dag-trusted-keys /root/trusted_keys.json --dag-require-signatures
```

Step 10. Pi 5, terminal 2 (bridge):

```bash
python mqtt_http_bridge.py --keepalive 600 --session placement --onboarding-csv experiments/results/placement/dist50/onboarding_pi5.csv --csv experiments/results/placement/dist50/eventos_bridge_pi5.csv
```

Step 11. Orange Pi, terminal 2 (bridge):

```bash
python mqtt_http_bridge.py --keepalive 600 --session placement --onboarding-csv /root/PINV01-24/resultados/placement/dist50/onboarding_opi.csv --csv /root/PINV01-24/resultados/placement/dist50/eventos_bridge_opi.csv
```

Step 12. Pi 5, terminal 3 (agent):

```bash
python experiments/dag_sync.py --local http://localhost:5000 --peer http://192.168.0.13:5000 --csv experiments/results/placement/dist50/sync_pi5.csv
```

Step 13. Orange Pi, terminal 3 (agent):

```bash
python experiments/dag_sync.py --local http://localhost:5000 --peer http://192.168.0.21:5000 --csv /root/PINV01-24/resultados/placement/dist50/sync_opi.csv
```

Both agents showed `offered 1, added 1`.

Step 14. Pi 5, terminal 4 (monitor):

```bash
python experiments/resource_monitor.py --node pi5 --csv experiments/results/placement/dist50/resources_pi5.csv
```

Step 15. Orange Pi, terminal 4 (monitor):

```bash
python experiments/resource_monitor.py --node opi --csv /root/PINV01-24/resultados/placement/dist50/resources_opi.csv
```

Step 16. Pi 5, terminal 5 (generator, about 15 minutes):

```bash
python experiments/load_generator.py --host localhost 192.168.0.13 --weights 50 50 --label dist50 --mode ramp --rates 2 5 10 20 40 --step-s 20 --settle 150 --csv experiments/results/placement/dist50/generator_dist50.csv
```

Step 17. Closing. Ctrl+C in this order, on both boards: the monitors (terminal 4), the agents (terminal 3), the bridges (terminal 2) and the servers (terminal 1). Then, Pi 5, terminal 5:

```bash
cond after experiments/results/placement/dist50/conditions.csv
```

```bash
chronyc tracking > experiments/results/placement/dist50/chrony_fin_pi5.txt
```

Orange Pi, terminal 5:

```bash
chronyc tracking > /root/PINV01-24/resultados/placement/dist50/chrony_fin_opi.txt
```

```bash
scp /root/PINV01-24/resultados/placement/dist50/* ricardo@192.168.0.21:/home/ricardo/PINV01-24/experiments/results/placement/dist50/
```

### Run 3: distributed by capacity (89/11)

These are the same as steps 7 to 17, with `distcap` instead of `dist50` in all paths and in `--label`, and with `--weights 89 11`.

Step 18. Folders, conditions and chrony. Pi 5, terminal 5:

```bash
mkdir -p experiments/results/placement/distcap
```

```bash
cond before experiments/results/placement/distcap/conditions.csv
```

```bash
chronyc tracking > experiments/results/placement/distcap/chrony_inicio_pi5.txt
```

Orange Pi, terminal 5:

```bash
mkdir -p /root/PINV01-24/resultados/placement/distcap
```

```bash
chronyc tracking > /root/PINV01-24/resultados/placement/distcap/chrony_inicio_opi.txt
```

Step 19. Pi 5, terminal 1 (server):

```bash
python run_server.py --classifier random_forest --feature-set resource --training-source catalog --training-samples 2000 --node-id pi5 --dag-key-file ~/dag_signing_key.hex --dag-trusted-keys ~/trusted_keys.json --dag-require-signatures
```

Step 20. Orange Pi, terminal 1 (server; about 12 s):

```bash
python run_server.py --classifier random_forest --feature-set resource --training-source catalog --training-samples 2000 --node-id opi --dag-key-file /root/dag_signing_key.hex --dag-trusted-keys /root/trusted_keys.json --dag-require-signatures
```

Step 21. Pi 5, terminal 2 (bridge):

```bash
python mqtt_http_bridge.py --keepalive 600 --session placement --onboarding-csv experiments/results/placement/distcap/onboarding_pi5.csv --csv experiments/results/placement/distcap/eventos_bridge_pi5.csv
```

Step 22. Orange Pi, terminal 2 (bridge):

```bash
python mqtt_http_bridge.py --keepalive 600 --session placement --onboarding-csv /root/PINV01-24/resultados/placement/distcap/onboarding_opi.csv --csv /root/PINV01-24/resultados/placement/distcap/eventos_bridge_opi.csv
```

Step 23. Pi 5, terminal 3 (agent):

```bash
python experiments/dag_sync.py --local http://localhost:5000 --peer http://192.168.0.13:5000 --csv experiments/results/placement/distcap/sync_pi5.csv
```

Step 24. Orange Pi, terminal 3 (agent):

```bash
python experiments/dag_sync.py --local http://localhost:5000 --peer http://192.168.0.21:5000 --csv /root/PINV01-24/resultados/placement/distcap/sync_opi.csv
```

Step 25. Pi 5, terminal 4 (monitor):

```bash
python experiments/resource_monitor.py --node pi5 --csv experiments/results/placement/distcap/resources_pi5.csv
```

Step 26. Orange Pi, terminal 4 (monitor):

```bash
python experiments/resource_monitor.py --node opi --csv /root/PINV01-24/resultados/placement/distcap/resources_opi.csv
```

Step 27. Pi 5, terminal 5 (generator, about 15 minutes):

```bash
python experiments/load_generator.py --host localhost 192.168.0.13 --weights 89 11 --label distcap --mode ramp --rates 2 5 10 20 40 --step-s 20 --settle 150 --csv experiments/results/placement/distcap/generator_distcap.csv
```

Step 28. Closing. Same Ctrl+C order as in step 17. Then, Pi 5, terminal 5:

```bash
cond after experiments/results/placement/distcap/conditions.csv
```

```bash
chronyc tracking > experiments/results/placement/distcap/chrony_fin_pi5.txt
```

Orange Pi, terminal 5:

```bash
chronyc tracking > /root/PINV01-24/resultados/placement/distcap/chrony_fin_opi.txt
```

```bash
scp /root/PINV01-24/resultados/placement/distcap/* ricardo@192.168.0.21:/home/ricardo/PINV01-24/experiments/results/placement/distcap/
```

### Wrap-up

Step 29. Pi 5, terminal 5:

```bash
cd ~/PINV01-24/experiments/results
```

```bash
python3 -m zipfile -c placement.zip placement
```

Step 30. The results were copied to the PC with `scp`.
