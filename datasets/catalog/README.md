# Catálogo de placas reales (v3)

Fuente de verdad del generador sintético basado en placas reales
(`pnp_ai/dataset_generator/catalog.py`). Proyecto PINV01-24 — FIUNA.

| Archivo | Papel |
|---|---|
| `catalogo_placas.csv` | 114 placas con sus datos de fábrica y fuente. Se edita a mano, **con un editor de texto, no con Excel** (Excel en español usa `;` como separador y reescribe el archivo). |
| `build_catalogo.py` | Validador: `python3 datasets/catalog/build_catalogo.py`. Tiene que dar 0 errores. Escribe `resumen_por_clase.md`. |
| `resumen_por_clase.md` | Salida del validador. |

## Reglas

1. `clase_hw` la define la familia del procesador principal; `clase_clasificador` es la clase sugerida para el clasificador. La evaluación compara tres etiquetados: `clase_clasificador`, `clase_hw` y `hw_unificado` (`clase_hw` con ESP32Device fusionado en Microcontroller).
2. `enlaces` = lo que trae la placa base de fábrica, separados por `;` (wifi, ethernet, bluetooth, lora, zigbee, cellular, serial). Los módulos opcionales van en `notas`.
3. RAM = SRAM/DRAM principal, sin PSRAM. Flash de SBC = eMMC integrada.
4. Los equipos industriales con Linux de propósito general son SingleBoardComputer; IoTGateway queda para equipos con SoC de router o diseño cerrado.
5. `serial` solo cuando la placa no tiene enlace de red, o para RS-232/485 industrial.

## Calibración (columnas medidas en el laboratorio)

`memoria_anunciada_bytes_medida`, `cpu_mhz_medido` y `enlace_medido` guardan lo que la placa **anunció** al registrarse
(`ESP.getHeapSize()` en ESP32, `MemTotal` y `cpuinfo_max_freq` en Linux) y por qué enlace. El generador estima con
esas filas la relación memoria anunciada / RAM nominal por (familia, enlace).

| Placa | Enlace | Anunciada (B) | Relación |
|---|---|---|---|
| Olimex ESP32-PoE | ethernet | 364040 | 0,684 |
| M5Stack Unit PoE CAM-W | ethernet | 376268 | 0,707 |
| ESP32-WROOM-32 (placa genérica) | wifi | 331488 | 0,623 |
| Ai-Thinker ESP32-CAM | wifi | 326880 | 0,614 |
| Waveshare ESP32-C3-Zero | wifi | 295580 | 0,722 |
| Raspberry Pi 5 8 GB | wifi | 8454012928 | 0,984 |
| Orange Pi Zero LTS 512 MB | ethernet | 508035072 | 0,946 |

Cada resultado guarda la versión del catálogo (primeros 12 dígitos del SHA-256 del CSV), así que cualquier cambio en
el archivo queda reflejado en los resultados.
