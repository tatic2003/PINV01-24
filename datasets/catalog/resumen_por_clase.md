# Resumen por clase (generado por build_catalogo.py)

RAM y flash en base binaria (1 KB = 1024 B). Los rangos solo consideran filas con el valor verificado.

## Por clase_clasificador

| clase_clasificador | filas | con CPU | CPU nominal min–max (MHz) | con RAM | RAM nominal min–max | alta/media/baja | revisar |
|---|---|---|---|---|---|---|---|
| ESP32Device | 34 | 34 | 96–240 | 34 | 320 KB–520 KB | 21/13/0 | 0 |
| SingleBoardComputer | 36 | 36 | 900–2400 | 36 | 256 MB–64 GB | 18/15/3 | 0 |
| IoTGateway | 16 | 16 | 400–1500 | 16 | 64 MB–8 GB | 3/13/0 | 0 |
| Sensor | 12 | 12 | 32–64 | 12 | 16 KB–256 KB | 2/10/0 | 0 |
| Microcontroller | 16 | 16 | 16–600 | 16 | 2 KB–1 MB | 12/4/0 | 0 |
| **Total** | 114 | 114 | | 114 | | 56/55/3 | 0 |

## Por clase_hw

| clase_hw | filas |
|---|---|
| ESP32Device | 34 |
| SingleBoardComputer | 45 |
| IoTGateway | 7 |
| Sensor | 7 |
| Microcontroller | 21 |

## Filas con clase_hw distinta de clase_clasificador (14)

| id | board_id | clase_clasificador | clase_hw |
|---|---|---|---|
| 41 | arduino_nano_33_ble_sense | Sensor | Microcontroller |
| 42 | arduino_mkr_wan_1310 | Sensor | Microcontroller |
| 50 | nordic_nrf52840_dk | Sensor | Microcontroller |
| 51 | ti_launchxl_cc26x2r1 | Sensor | Microcontroller |
| 52 | heltec_cubecell_htcc_ab01 | Sensor | Microcontroller |
| 85 | seeed_reterminal_4gb | IoTGateway | SingleBoardComputer |
| 86 | seeed_reterminal_dm | IoTGateway | SingleBoardComputer |
| 88 | kunbus_revpi_connect_4_8gb | IoTGateway | SingleBoardComputer |
| 89 | kunbus_revpi_connect_s_1gb | IoTGateway | SingleBoardComputer |
| 90 | kunbus_revpi_core_3_plus_1gb | IoTGateway | SingleBoardComputer |
| 91 | moxa_uc_2101_lx | IoTGateway | SingleBoardComputer |
| 92 | moxa_uc_8112_lx | IoTGateway | SingleBoardComputer |
| 93 | moxa_uc_8220 | IoTGateway | SingleBoardComputer |
| 100 | advantech_ecu_1251_v2 | IoTGateway | SingleBoardComputer |

## Muestra aleatoria para estimar la tasa de error

Semilla 20261006, 8 filas elegidas al azar entre las 114 con revisar=no (random.Random(semilla).sample sobre las filas en orden de id).

| id | board_id | confianza | fuente_url |
|---|---|---|---|
| 11 | olimex_esp32_poe | alta | https://www.olimex.com/Products/IoT/ESP32/ESP32-POE/open-source-hardware |
| 26 | seeed_studio_xiao_esp32s3 | alta | https://wiki.seeedstudio.com/xiao_esp32s3_getting_started/ |
| 37 | arduino_uno_r4_minima | alta | https://docs.arduino.cc/hardware/uno-r4-minima/ |
| 64 | raspberry_pi_400_4gb | alta | https://www.raspberrypi.com/products/raspberry-pi-400/specifications/ |
| 66 | raspberry_pi_5_4gb | alta | https://www.raspberrypi.com/products/raspberry-pi-5/ |
| 72 | orange_pi_zero_3_1gb | media | http://www.orangepi.org/html/hardWare/computerAndMicrocontrollers/details/Orange-Pi-Zero-3.html |
| 87 | seeed_sensecap_m2_multi_platform | media | https://wiki.seeedstudio.com/Network/SenseCAP_Network/SenseCAP_M2_Multi_Platform/SenseCAP_M2_Multi_Platform_Overview/ |
| 101 | waveshare_esp32_c3_zero | media | https://www.waveshare.com/wiki/ESP32-C3-Zero |
