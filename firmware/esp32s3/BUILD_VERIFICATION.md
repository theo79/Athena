> Historical bring-up verification record. Physical acceptance subsequently
> passed, as reported by the prototype owner on 31 October 2026. Earlier
> "pending" or "not flashed" statements describe the state at that development
> step. The current milestone status is in the
> [Phase A completion report](../../M3_3_PHASE_A_COMPLETION_REPORT.md).

Current power behavior: see [USB standby and PWR wake verification](STANDBY_VERIFICATION.md).

Latest firmware update: see [PWR shutdown verification](SHUTDOWN_VERIFICATION.md).

# LCD orientation correction â€” 31 October 2026

Physical hardware boot was reported successful by the user: 8 MB PSRAM memory
test passed, SYS_EN HIGH, Ready reached and the original logo appeared, but the
whole frame was upside down. Corrected the panel MADCTL orientation through
`esp_lcd_panel_mirror(panel, false, false)`, toggling both prior `true` flags.
This rotates all logo/text output consistently by 180 degrees relative to the
previous firmware. No source image or generated screen content was rotated.

Portrait 240 x 280, `swap_xy=false`, RGB565 byte order, inversion, colors and
layout remain unchanged. The gap stays (0,20): the ST7789's 320-row memory uses
rows 20..299 inclusive, a centered range invariant under a 180-degree reversal.
No offset adjustment is needed for this orientation correction.

Rebuilt successfully using ESP-IDF v6.1 on Windows. No compiler/linker warnings.
Application remains 335,760 bytes (0x51f90), with 84% of its 2 MiB partition free.
Offline executable checksums/hashes and partition MD5/layout checks passed.
Flash remains DIO/80 MHz/16 MB, with the same bootloader/table/app offsets.
Current application SHA256: `e83420111c299f26047f432899d79a842898131300435295b86f4838935229d6`.

Source-hash comparison confirms the original PNG, asset converter, version,
startup/power-hold code, battery monitoring and configuration are unchanged.
Changes for this correction: main/display.c, README.md and this report only.
No host M3.2 files were modified.

Host regression suite: 969 passed, 1 skipped; one existing Google genai
third-party deprecation warning. Host `python -m pip check` passed. Windows
checks ran; Linux was not exercised. New text whitespace checks and
`git -c core.whitespace=cr-at-eol diff --check` passed.

No flash, erase, eFuse modification, commit or push was performed for this fix.
Physical confirmation of upright output awaits a separately authorized flash.
Check the entire logo, ATHENA, v0.1.0 and lower status lines for upright output
and unchanged placement; confirm no missing/shifted edge rows.

---

The following is the historical initial-build report. Its binary hashes and
orientation description refer to the original, pre-correction build.

# M3.3 Phase A build verification

Verified on Windows, 31 October 2026, branch `feature/embodied-agent`.
SDK installation: `C:/esp/v6.1/esp-idf`, Git tag **v6.1**, Xtensa GCC 15.2.0,
esptool 5.6.0. Build Python 3.11.15 is the SDK environment; the existing host
application retains its separate Python environment and requirements.

## Result

PASS: clean `idf.py set-target esp32s3`, `idf.py build`, and `idf.py size`.
No board connection, flash, erase, eFuse operation, commit or push was performed.
Only the new firmware directory is untracked; existing tracked host files have
no diff. No credentials, factory backup or generated binary is included in the
source file inventory. Build products and generated sdkconfig are ignored.

The first configure attempt exposed an add_custom_command ordering error; fixed
by registering the component before adding the custom command. Clean target
configuration and compilation then passed from sdkconfig.defaults.

## Created source files

- `.gitignore`
- `CMakeLists.txt`
- `README.md`
- `assets/athena-icon.png`
- `main/CMakeLists.txt`
- `main/battery.c`
- `main/battery.h`
- `main/board.h`
- `main/display.c`
- `main/display.h`
- `main/main.c`
- `main/power.c`
- `main/power.h`
- `partitions.csv`
- `requirements-build.txt`
- `sdkconfig.defaults`
- `tools/convert_logo.py`
- `BUILD_VERIFICATION.md` (this report)

## Binary and partition validation

| Generated artifact | Flash offset | File bytes | SHA256 |
| --- | --- | ---: | --- |
| `bootloader/bootloader.bin` | `0x0` | 21,168 | `15a0dd2472993135f3739fbeab31a247a54379a2c8e51323ca6caead5b2a8b89` |
| `partition_table/partition-table.bin` | `0x8000` | 3,072 | `72fa70ce08ad0a9e8710f0dc52201f995b23408ab92cc233bef4ec3c35ebef05` |
| `athena.bin` | `0x10000` | 335,760 | `922acf64732ba9f7cd797a154a8cb3bca877bed590efbbdee6fac96bbb65955b` |

Application: **335,760 bytes (`0x51f90`)**, 16.01% of its 2,097,152-byte
factory partition; **1,761,392 bytes (`0x1ae070`) free**, approximately 84%.
Bootloader: **21,168 bytes (`0x52b0`)**; 11,600 bytes remain before 0x8000.
The image-size report excludes some file padding: 335,644 bytes.
Static DIRAM: 57,780 bytes (16.91% of the reported 341,760-byte region).
Display upload uses a separate 9,600-byte internal DMA buffer at runtime.
Runtime heap, detected PSRAM and boot timing remain hardware-test items.

Partition-table binary decoded and verified with ESP-IDF gen_esp32part.py;
its MD5 was independently checked. NVS: 0x9000, 0x6000 bytes (reserved/unused).
Factory app: 0x10000, 0x200000 bytes. No overlapping partitions; allocation ends
at 0x210000, inside the 16 MB device. No OTA or filesystem partition.

Both executable images were inspected offline with:

```sh
python -m esptool --chip esp32s3 image-info build/bootloader/bootloader.bin
python -m esptool --chip esp32s3 image-info build/athena.bin
python "$IDF_PATH/components/partition_table/gen_esp32part.py" build/partition_table/partition-table.bin
```

Both image checksums and SHA256 validation hashes are valid. Headers identify
ESP32-S3 (chip ID 9), DIO, 80 MHz, 16 MB and supported silicon revisions v0.0
through v0.99, covering the supplied v0.2 hardware. App descriptor: athena 0.1.0,
ESP-IDF v6.1. The flasher manifest agrees with all three offsets above.
Secure boot and flash encryption are disabled in the generated configuration;
no device security settings were read or changed.

## Hardware and console review

LCD: GPIO DC4/CS5/CLK6/MOSI7/RST8/BL15; SPI3 mode 0 at 40 MHz,
RGB order, RGB565 big-endian bytes, portrait, mirror X/Y, gap (0,20), inversion
enabled. Native esp_lcd ST7789 reset/init follows the manufacturer's native-panel
example. Backlight stays off until all stripes have completed asynchronously.

SYS_EN GPIO41 is latched HIGH as the first app_main operation; GPIO40 is an
input with no pulls and no button actions. Battery GPIO1 uses ADC1 channel 0,
12 dB attenuation, curve-fitting calibration and a 32-reading average, scaled
by the V2 divider ratio of 3. Missing calibration produces raw counts only.
No percentages, charging detection or USB-dependent boot waits exist.

Configuration confirms octal PSRAM at 80 MHz with boot initialization and memory
test. Physical 8 MB detection cannot be confirmed until authorized boot testing.
CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG=y, CONFIG_ESP_CONSOLE_SECONDARY_NONE=y,
CONFIG_ESP_CONSOLE_UART_NUM=-1. This selects the native hardware console, without
TinyUSB or a runtime USB CDC stack. Physical COM4 output is not yet tested.

## Logo verification

The copied PNG is byte-identical to the supplied root image. SHA256:
`228b5c450d06f86710e13877df62ec58c495f54e9e12d94bbebe9aa21b36e646`.
The complete square original is fitted to 208 x 208, without stretching/cropping,
on a white 240 x 280 screen. Original colors are resized and quantized to RGB565.
Build-generated screen: 134,400 bytes, constant flash data, no runtime decoder.
The actual generated PNG preview was visually inspected: original owl/A,
ATHENA, version, hardware and Ready status are visible within the frame.

## Dependencies and checks

Direct firmware requirements: esp_driver_gpio, esp_driver_spi, esp_lcd, esp_adc,
esp_psram, plus the normal ESP-IDF runtime. Minimal component build enabled.
ESP-IDF brings transitive driver/HAL, boot/update/security and mbedTLS components;
their presence does not initialize those peripherals or enable network services.
Build-component list checked: no esp_wifi, lwip, bt, lvgl, arduino or WebSocket
client. Pillow 11.3.0 is only a host build dependency.

Existing host regression suite: **969 passed, 1 skipped**, in 140.65 seconds:

```powershell
.\.venv\Scripts\python.exe -m pytest -x -q --tb=short --basetemp C:\Users\theoc\Documents\ChatGPT\Athena\m33-test-temp -o cache_dir=C:\Users\theoc\Documents\ChatGPT\Athena\m33-pytest-cache
```

The initial default-temp run produced repeated fixture setup errors and was
stopped; the full suite passed with explicit writable temp/cache paths, without
changing code, tests or assertions. Both host and SDK Python environments passed
`python -m pip check`. `git -c core.whitespace=cr-at-eol diff --check` passed.
New text files were separately checked for trailing whitespace and final newline.
Windows was exercised; Linux builds/tests were not run in this session.

## Warnings and remaining physical risks

- No firmware compiler or linker warnings in the successful build. SDK Kconfig
  emits deprecation notices about its own hex literals and a bootloader-context
  interrupt watchdog default notification (800 ms retained versus 300 ms).
- The host suite reports one Google genai third-party Python deprecation warning.
- ROM/bootloader/IDF startup precedes app_main power hold. Hold PWR until the LCD
  is visible on the first cold battery test, then release and verify hold. Cold
  boot, reset and USB-to-battery handover have not been tested. No shutdown gesture
  or battery undervoltage policy is implemented in this phase.
- Divider tolerance, calibrated ADC accuracy and USB/no-battery behavior require
  comparison with a meter; voltage is not a state-of-charge or charging indicator.
- LCD orientation, colors, rounded-corner visibility and native USB logging remain
  unverified on hardware. The generated preview is not evidence of physical output.

See README.md for exact controlled-flash commands, ROM-download recovery and the
USB, battery hold, handover and LCD acceptance checklist. **Stop point: build
verification complete. Flashing still requires separate authorization.**
