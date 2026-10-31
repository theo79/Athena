# M3.3 Phase A — completion report

**Status: COMPLETED. Development paused until explicitly resumed.**

Accepted on 31 October 2026. Branch: `feature/embodied-agent`.
Firmware: **v0.1.0**; ESP-IDF **v6.1**. Hardware: Waveshare
ESP32-S3-LCD-1.69 **non-touch V2**, 16 MB flash, 8 MB embedded PSRAM,
single-cell 3.7 V / 1000 mAh Li-Po. This report supersedes earlier historical
bring-up notes whose physical checks were still pending at the time.

## Accepted physical prototype

The prototype owner reported successful physical acceptance, including:

- USB flash download and native USB serial monitoring.
- ESP32-S3 boot and PSRAM initialization/memory test.
- Original official Athena owl/A logo, correct orientation and colors.
- Battery voltage ADC measurements and GPIO41 battery power hold.
- Battery-only startup/shutdown and continuous 3-second PWR shutdown.
- USB-powered software standby and fresh PWR press wake/restart.
- USB reconnection and USB/battery power transitions.

These are user-reported physical results. Automated native tests cover button
logic and GPIO/restart call ordering; they do not replace electrical tests.
This finalization session performed no firmware flashing or eFuse operations.
Two 16,777,216-byte factory backup files were independently size/hash checked:
they are identical and remain outside the repository in the external backup
directory. They are not included in the source commit or packages.

## Hardware foundation delivered

The separate `firmware/esp32s3/` project uses native ESP-IDF esp_lcd ST7789 support,
ADC calibration and simple FreeRTOS polling. There is no Arduino, LVGL, Wi-Fi,
provisioning, audio, WebSocket client or runtime PNG decoder in Phase A.

| Function | Final configuration |
| --- | --- |
| Flash/PSRAM | 16 MB DIO flash at 80 MHz; 8 MB octal PSRAM at 80 MHz |
| LCD SPI3 | Mode 0, 40 MHz; DC4, CS5, CLK6, MOSI7, RST8, BL15 |
| LCD format/orientation | 240 x 280 portrait, RGB565 big-endian, RGB order; mirror false/false, XY swap false, gap (0,20), inversion enabled |
| Battery hold | GPIO41 HIGH as the first application action |
| PWR input | GPIO40 active LOW; external board pull-up, internal pulls disabled |
| Battery voltage | GPIO1 / ADC1 channel 0; 32-reading average; calibrated mV x3 for the V2 divider |
| Console | Native USB Serial/JTAG; no secondary UART console |

The original `assets/athena-icon.png` is byte-identical to the supplied root PNG.
Pillow 11.3.0 is a host-only build dependency. CMake regenerates the RGB565 data
when the PNG/converter changes. The complete image is proportionally fitted on
white, with the agreed ATHENA/version/hardware/Ready layout. The final generated
startup preview is byte-identical to the previously accepted preview. The full
screen is 134,400 flash bytes; the shutdown message strip is 19,200 flash bytes.
One 9,600-byte internal DMA stripe is reused and freed after IO drain on shutdown.
No battery information is rendered on the LCD.

## Battery-off versus USB software standby

| State | SYS_EN | Firmware/display behavior | PWR action |
| --- | --- | --- | --- |
| Running | HIGH | Static Athena screen, periodic battery voltage log | 30 ms debounce plus continuous 3-second hold starts shutdown |
| Battery-only off, PWR released | LOW | Battery switch removes MCU supply; execution stops | New PWR press cold-boots; hold until the screen appears |
| USB standby | LOW | USB supplies MCU; LCD/backlight off, ADC/LCD resources cleaned up; only PWR polling at application level | Stable release, then fresh debounced press raises SYS_EN before esp_restart() |

Shutdown shows `Shutting down...` for 750 ms before cleanup and hold release.
The button's separate hardware path can still supply battery power while held.
USB standby is not electrical USB disconnection or deep sleep. With PWR released,
removing USB during standby leaves the battery path off. If supply disappears,
execution stops naturally; no standby state is persisted. Reconnecting USB to
an unpowered board boots automatically.

Standby uses the existing main task, blocking 10 ms (at least one tick) between
samples. It first requires a stable 30 ms release of the original shutdown press,
then a fresh 30 ms press. Wake is latched once; SYS_EN must be set HIGH before
esp_restart(). The boot release guard prevents a still-held wake button from
triggering another shutdown cycle. No LCD/ADC initialization occurs in standby.
Normal short presses do not accumulate hold time, and reset clears timing state.

## Final local verification

All checks passed on Windows:

- Clean `idf.py fullclean build` using installed ESP-IDF v6.1 / Xtensa GCC 15.2.0.
- Offline executable checksum/SHA256 validation, partition-table MD5, flash
  arguments and non-overlapping layout checks.
- Native production-C shutdown and standby suites, compiled with warnings as
  errors using Zig 0.15.2; GPIO/esp_restart doubles test ordering and failure paths.
- Full existing host regression: **969 passed, 1 skipped**, 144.49 seconds.
- Host and firmware-build Python environments: `python -m pip check` passed.
- Host wheel and source distribution built successfully; 40 wheel members and
  46 sdist members audited for private data, credentials and generated artifacts.
  Packaged host Python modules match source byte-for-byte. Packaging safety tests
  are included in the full host suite. A new frozen Windows executable is not a
  deliverable of this independent firmware milestone.
- Final source/staging checks exclude backups, generated binaries/build folders,
  SDK configuration output, temporary files, credentials and local environments.
  Whitespace review uses `git -c core.whitespace=cr-at-eol diff --check`.

The Python host's application code, dependencies, packaging configuration and
M3.2 gateway/protocol behavior are unchanged. Existing tracked file changes are
documentation only; all firmware implementation and test files are isolated in
the new firmware directory. No new CI job is introduced in this phase.

### Final local build artifacts (ignored; not committed)

| Artifact | Offset | Bytes | SHA256 |
| --- | --- | ---: | --- |
| `bootloader/bootloader.bin` | `0x0` | 21,168 | `8c37f85f218efab5002636201e79a0893aa8f6f40c9ccb9146a983c4caf6470c` |
| `partition_table/partition-table.bin` | `0x8000` | 3,072 | `72fa70ce08ad0a9e8710f0dc52201f995b23408ab92cc233bef4ec3c35ebef05` |
| `athena.bin` | `0x10000` | 358,112 | `e197887ed639c042adf1d9c38e88fe0b463ef177a0aec2a4b07b27bcb47c9eeb` |

Application: **358,112 bytes (`0x576e0`)**, with **1,739,040 bytes (`0x1a8920`)
free** in the 2 MiB factory partition (approximately 83%). NVS reserves 24 KiB
at 0x9000 and is unused. Factory application starts at 0x10000 and ends its
allocated region at 0x210000. No OTA or filesystem partition is present.
Image headers confirm ESP32-S3, DIO/80 MHz/16 MB. Security configuration is
unchanged: no eFuse/security provisioning or flash-security operation occurred.

There were no firmware compiler/linker warnings. The SDK emits its existing
Kconfig hex-literal deprecation notices and a bootloader-context watchdog-default
notification (800 ms retained versus 300 ms). The host suite has one existing
Google genai third-party Python deprecation warning. None blocked verification.

## CI coverage and publication

`.github/workflows/tests.yml` runs the Python host tests and pip check on Windows
and Ubuntu with Python 3.12/3.13/3.14, plus Debian/Fedora/Arch distribution Python.
**It does not build the ESP-IDF firmware, convert/test assets, or execute the
native C button suites.** Firmware checks above were local Windows checks, not
firmware CI. The Actions results for the exact pushed commit are verified and
reported separately with its SHA in the final milestone handoff; configured
jobs alone are not evidence that the pushed run passed.

The authorized commit message is:
`feat: add Athena ESP32-S3 hardware foundation (M3.3 Phase A)`.
The publication target is only `origin/feature/embodied-agent`; no merge, default
branch change, tag or firmware release upload is part of this checkpoint.

## Remaining limits and risks

- Physical acceptance establishes prototype operation, not exhaustive brownout,
  arbitrary crash, temperature, battery lifetime, or long-duration reliability
  qualification. Software standby has no specified low-current target.
- Battery voltage remains an ADC/divider measurement with component tolerances;
  no percentage, charging-state, battery-presence detection or undervoltage policy
  is inferred. Runtime duration for the 1000 mAh pack has not been characterized.
- GPIO41 continuity during unusual restart/power-removal timing and electrical
  fault behavior remain engineering qualification items despite accepted normal
  power transitions. A failed wake GPIO write suppresses restart; RESET recovers.
- Firmware CI and Linux firmware/native-test execution are not provided by the
  current workflow. GitHub's host CI results are reported at final handoff.

## Completion checkpoint and next scope

**M3.3 Phase A is complete. Athena development is paused until explicitly resumed.**
Phase B is planned only: Wi-Fi, persistent device identity, provisioning, and
authenticated communication with the existing M3.2 gateway. No such feature is
implemented or activated by this commit.

Future visual direction (planned, not part of the current static bring-up UI):

- Boot: official Athena logo.
- Setup: Wi-Fi and Ollama/cloud provider status.
- Live: calm, friendly animated eyes.
- Shutdown: graceful power-off.

See [firmware README](firmware/esp32s3/README.md) for build/monitor/recovery and
the repeatable hardware checklist. Earlier detailed bring-up records remain
under the firmware directory as historical evidence.
