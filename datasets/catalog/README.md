# Catalogue of real boards

Source of truth of the announcement generator built on real boards (`pnp_ai/dataset_generator/catalog.py`), which trains the PnP-AI classifier of the server and of every evaluation.

| File | Role |
|---|---|
| `catalogo_placas.csv` | 114 commercial boards with their datasheet values, source and date of consultation. Edit it with a text editor, not with Excel: Excel in Spanish uses `;` as separator and rewrites the file. |
| `build_catalogo.py` | Validator: `python3 datasets/catalog/build_catalogo.py`. It must report 0 errors, and it writes `resumen_por_clase.md`. |
| `resumen_por_clase.md` | Output of the validator: rows, CPU and RAM ranges and confidence per class. |

Column names and notes are in Spanish, as the catalogue was built.

## Classes

Each board has two class columns:

- `clase_hw`: the class given by the main processor and the hardware, which is the labelling used by the server and by the evaluations of the article (labelling `hw`, the default). By this column the catalogue has 34 `ESP32Device`, 45 `SingleBoardComputer`, 7 `IoTGateway`, 21 `Microcontroller` and 7 `Sensor`.
- `clase_clasificador`: an earlier class suggestion, kept for reference. The generator can still use it (labelling `clasificador`), and also `hw_unificado`, which is `clase_hw` with `ESP32Device` merged into `Microcontroller`.

## Rules

1. `enlaces` lists the links of the base board as sold, separated by `;` (wifi, ethernet, bluetooth, lora, zigbee, cellular, serial). Optional modules go in `notas`.
2. RAM is the main SRAM or DRAM, without PSRAM. The flash of a single-board computer is its integrated eMMC.
3. Industrial computers running general-purpose Linux are `SingleBoardComputer`; `IoTGateway` is kept for hardware with a router SoC or a closed design.
4. `serial` is listed only when the board has no network link, or for industrial RS-232/485.

## Calibration (columns measured in the laboratory)

`memoria_anunciada_bytes_medida`, `cpu_mhz_medido` and `enlace_medido` hold what the board **announced** when it registered (`ESP.getHeapSize()` on the ESP32 boards, `MemTotal` and `cpuinfo_max_freq` on Linux) and over which link. The generator estimates from these rows the ratio of announced memory to nominal RAM per chip family and link.

| Board | Link | Announced (B) | Ratio |
|---|---|---|---|
| Olimex ESP32-PoE | ethernet | 364040 | 0.684 |
| M5Stack Unit PoE CAM-W | ethernet | 376268 | 0.707 |
| ESP32-WROOM-32 (generic board) | wifi | 331488 | 0.623 |
| Ai-Thinker ESP32-CAM | wifi | 326880 | 0.614 |
| Waveshare ESP32-C3-Zero | wifi | 295580 | 0.722 |
| Raspberry Pi 5 8 GB | wifi | 8454012928 | 0.984 |
| Orange Pi Zero LTS 512 MB | ethernet | 508035072 | 0.946 |

## Version

Every result records the version of the catalogue: the first 12 hexadecimal digits of the SHA-256 of `catalogo_placas.csv`, computed independently of line endings. The results of the article were produced with version `8f8756c70cae`. Any change to the file, even in a note, changes the version, so edit it only to produce new results.
