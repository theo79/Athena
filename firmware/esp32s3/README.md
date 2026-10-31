# Athena ESP32-S3 firmware v0.1.0

**M3.3 Phase A complete.** Physical hardware acceptance passed, as reported by
the prototype owner on 31 October 2026. See the repository's
[completion report](../../M3_3_PHASE_A_COMPLETION_REPORT.md) for verification,
scope, CI limits and the development pause checkpoint.

Standalone ESP-IDF v6.1 project for Waveshare **ESP32-S3-LCD-1.69 non-touch V2**.
The existing Python host is independent of this project. No Arduino, LVGL,
network stack, provisioning, audio, runtime image decoder, or managed components.

## Hardware and startup

| Function | Configuration |
| --- | --- |
| MCU | ESP32-S3 revision v0.2; target esp32s3 |
| Flash | 16 MB, DIO, 80 MHz |
| PSRAM | Embedded 8 MB octal, 80 MHz, boot memory test |
| ST7789V2 | 240 x 280, RGB565, RGB order, MSB-first pixel bytes |
| SPI3 | Mode 0, 40 MHz; DC 4, CS 5, CLK 6, MOSI 7, RST 8 |
| Orientation | Portrait, no XY swap, mirror X/Y both false, gap X=0/Y=20, inversion on |
| Backlight | GPIO15, active HIGH; enabled after frame upload |
| Power hold | GPIO41 SYS_EN HIGH as first action in app_main |
| Power button | GPIO40 SYS_OUT active LOW, external 10 kohm pull-up, polling/debounce |
| Battery | GPIO1 = ADC1 channel 0, 12 dB attenuation, 32-sample average |
| Divider | Vbat = calibrated ADC mV x 3; V2 200 kohm/100 kohm |
| Console | Native USB Serial/JTAG primary console; no secondary UART console |

Power hold is asserted before application logging, allocations, ADC or display
initialization. ROM, bootloader and IDF startup (including PSRAM initialization)
run before app_main: during a cold battery boot, hold PWR until the screen is
visible, then release. Battery boot and USB-to-battery handover passed physical
acceptance testing. This phase does not alter the bootloader's GPIO initialization.
SYS_EN stays asserted until an explicit debounced shutdown. The accepted power
transitions are listed below; arbitrary crash/brownout fault recovery has not
been exhaustively qualified. There is no USB-host wait.

## Battery-off versus USB standby

| State | SYS_EN | LCD/backlight and battery sampling | PWR behavior |
| --- | --- | --- | --- |
| Running on USB or battery | HIGH | Normal static screen and periodic ADC logging | Debounced 3-second hold shuts down |
| Battery-only, PWR released after shutdown | LOW | MCU execution stops as hardware removes supply | A new PWR press cold-boots; hold through startup |
| USB-powered software standby | LOW | Screen/backlight off; ADC/LCD resources released; only PWR polling at application level | Fully release the shutdown press, then a fresh debounced press raises SYS_EN and restarts |

PWR itself can maintain the battery switch while physically held. USB standby is
not electrical power-off or deep sleep: USB continues to supply the MCU and its
IDF/FreeRTOS runtime. Removing USB in standby with PWR released leaves the battery
path off. No USB detection hardware or additional peripherals are required.

## PWR button shutdown

The official V2 schematic's KEYS block maps SYS_OUT to GPIO40 and SYS_EN to
GPIO41. R20 (10 kohm) pulls SYS_OUT up to 3.3 V; PWR/Key2 pulls it LOW through
D2. GPIO40 is input-only, with internal pulls disabled because the board supplies
the pull-up. SYS_EN HIGH drives T2 to maintain Q5's battery power path. Pressing
PWR also supplies a separate hardware turn-on path through D3.

The main task samples GPIO40 every 10 ms using FreeRTOS delay-until, without
busy-waiting. It must first see a 30 ms stable release after startup/reset.
Then a fresh press is debounced for 30 ms, followed by a continuous 3-second
hold. A sampled release immediately cancels the hold; short presses do not
accumulate time. The startup button hold is ignored until release, including
when RESET is pressed while PWR is held. Firmware v0.1.0 is unchanged.

Shutdown runs once: stop normal battery polling, upload `Shutting down...` in
the bottom status area, leave it visible for 750 ms, delete ADC calibration/unit,
switch backlight GPIO15 LOW, switch the panel off/sleep, drain/delete LCD SPI IO,
free the DMA stripe, semaphore and bus, then drive SYS_EN GPIO41 LOW. Cleanup
errors are logged without deliberately resetting or aborting the power release.
The 9,600-byte DMA stripe is retained during normal operation for this message,
then freed after SPI IO has drained. The startup image pixels remain unchanged;
the shutdown strip is generated at build time (19,200 flash bytes).

**Release PWR after the message/backlight goes dark.** The pressed button can
keep the hardware battery switch on even after SYS_EN is LOW. USB power can
also keep the board energized. If execution continues, the existing main task
enters minimal software standby: only GPIO40 polling and debounce remain active
at the application level. Battery sampling and LCD activity stop; no extra task,
deep sleep, USB detection or direct peripheral reinitialization is used.

Standby waits for a complete 30 ms released PWR level, then requires a fresh
30 ms pressed level. The original shutdown hold cannot wake the board. A valid
wake press latches a single wake event, sets SYS_EN GPIO41 HIGH, then calls
`esp_restart()` for a clean boot. If setting SYS_EN fails, restart is suppressed
and the one-shot latch prevents repeated retries; use RESET for recovery.
After reboot, the existing normal-operation release guard ignores the still-held
wake press until it is released, preventing another shutdown/restart cycle.

The loop blocks for 10 ms per sample (at least one RTOS tick for any tick-rate
configuration). No periodic standby logs, battery reads or LCD redraws occur.
The ESP-IDF/FreeRTOS runtime and native USB console remain initialized: this is
software standby, not a deep-sleep or specified low-current operating mode.

Disconnecting USB in standby leaves SYS_EN LOW, so the battery path stays off
once PWR is released. If all supply disappears, execution stops naturally; no
standby state is saved. A subsequent battery PWR press uses the existing cold
boot path. Keep PWR held until the screen appears, then release. Connecting USB
from an unpowered state still boots automatically. If USB is unplugged while
PWR is held, the button itself may continue to power the board until released.
This is hardware power-hold control, not a switch that disconnects USB supply.

See [standby verification](STANDBY_VERIFICATION.md) for build results and the exact
physical acceptance checklist, including USB/battery transition tests.

ADC uses ESP-IDF curve-fitting calibration when the chip supports it. If it is
unavailable, only raw ADC counts are logged, never an invented voltage. Divider
and ADC tolerances remain: compare against a meter. USB with no battery may still
produce a voltage at the battery node; this is not battery-presence or charging
detection. No percentage, state of charge, charging status, or low-voltage policy
is inferred. The 10-second sample log does not redraw the static screen.

## Official identity and build-time asset

`assets/athena-icon.png` is a byte-for-byte copy of the supplied repository-root
PNG. `tools/convert_logo.py` composites transparency onto white, fits the complete
image within 208 x 208 without cropping or stretching, and centers it on a white
240 x 280 frame. Original charcoal, gold and white colors are retained, subject
to resizing and RGB565 quantization. Pillow is a host build dependency only.
The bundled Pillow font adds ATHENA, v0.1.0, Hardware: ESP32-S3 and Status: Ready.
Generated C and a PNG preview live in `build/esp-idf/main/startup_screen.*`.
The uncompressed static frame occupies 134,400 flash bytes; transfer uses one
9,600-byte internal DMA stripe, waiting for completion before reuse. No full
framebuffer or image decoding is needed in RAM. A changed PNG/converter triggers
regeneration through CMake's explicit dependencies.

## Build and monitor

Activate an ESP-IDF **v6.1** environment using its supported host setup. Then:

```sh
python -m pip install -r requirements-build.txt
idf.py set-target esp32s3
idf.py build
idf.py size
```

Generated `sdkconfig` and build output are ignored; reproducible settings are in
`sdkconfig.defaults`. `set-target` cleans only this project's build/configuration;
it does not communicate with or erase the board. Do not use it unnecessarily on
a configured project with local menuconfig changes.

For this Windows installation, initialize a PowerShell session with:

```powershell
. C:\Espressif\tools\Microsoft.v6.1.PowerShell_profile.ps1
$env:PYTHONUTF8 = '1'
Set-Location C:\Users\theoc\Documents\Athena\firmware\esp32s3
```

Use `idf.py -p COM4 monitor` after a separately authorized flash. Quit monitor
with Ctrl+]. On other hosts substitute the USB Serial/JTAG device path.
ROM logs may precede application logs. COM numbering may change after reset.

## Flash layout

| Artifact/partition | Offset | Allocated size |
| --- | --- | --- |
| Bootloader | 0x000000 | before partition table |
| Partition table | 0x008000 | 0x1000 |
| NVS (reserved, unused) | 0x009000 | 0x6000 |
| Alignment gap | 0x00f000 | 0x1000 |
| Factory application | 0x010000 | 0x200000 (2 MiB) |
| Unallocated | 0x210000 | through 0xffffff |

No OTA, filesystem, credentials, or factory backup is embedded. No NVS code is
initialized. A controlled flash overwrites sectors for the three generated
images, including the factory partition table; it is not a whole-flash erase.
The rest of the existing flash is not intentionally erased. Existing data there
is not interpreted by this application. Full factory backups remain outside Git.

## First controlled USB flash â€” ONLY after separate authorization

Close applications holding COM4, connect USB with a data cable, and confirm the
port and the two external 16 MB backups. Rebuild from this directory, then:

```powershell
idf.py build
idf.py -p COM4 -b 460800 flash
idf.py -p COM4 monitor
```

The `flash` command writes the bootloader, partition table and application at the
offsets recorded in `build/flasher_args.json`. Never add erase-flash, force,
eFuse, encryption or secure-boot commands. Flashing necessarily performs sector
erases for the written images. The prototype owner has flashed and accepted the final firmware. The finalization
review does not execute flashing commands.

## Recovery

If normal download/reset fails: keep USB connected, hold BOOT, tap RESET, release
BOOT, and recheck the enumerated port. This enters the ROM downloader. Close the
monitor before another flash. Retry at a lower baud rate if needed. Do not modify
eFuses/security settings or run a whole-flash erase.

Factory restoration requires separate authorization: select and hash-check one
of the previously verified complete 16 MB backups outside this repository, enter
the ROM downloader, then use ESP-IDF's bundled esptool to write that exact image
at offset 0x0 with the correct chip/port. Keep the backup out of this project.
Do not restore from the manufacturer's generic demo in place of the user's backup.

## Physical acceptance checklist

The prototype owner confirmed successful physical acceptance on 31 October 2026:
USB flashing/monitoring, boot/PSRAM, official logo/orientation/colors, battery ADC,
SYS_EN hold, battery startup/off, 3-second shutdown, USB standby/fresh-press wake,
USB reconnection and power transitions. The following is the retained repeatable
acceptance checklist. This is user-reported hardware evidence; automated native
tests exercise button logic and ordering with GPIO/restart doubles.

- USB power: boots without a monitor attached; monitor shows v0.1.0, Ready,
  detected 8 MB PSRAM and calibrated battery mV or an explicit raw-only fallback.
- LCD: static upright owl/A fully visible, original proportions, charcoal/gold
  colors, white background; ATHENA and version centered beneath it; no shifted
  rows, swapped red/blue, flicker or stale boot pixels.
- Battery: with a charged, correctly connected 3.7 V single-cell pack, hold PWR
  through boot, release after the screen appears; verify operation continues.
- Handover: boot on USB with battery attached, disconnect USB, verify LCD stays
  on; reconnect and observe logs. No USB-host wait or unintentional shutdown.
- Compare logged battery voltage with a meter at the pack; repeat with USB
  connected. Do not interpret USB/no-battery readings as battery status.
- Test RESET and cold battery boot while holding PWR beyond 3 seconds: no shutdown
  until PWR has been released, then freshly pressed for a full hold.
- Short presses and repeated short presses: no shutdown, battery logging continues.
- Battery-only: release after boot, hold PWR for 3 seconds after debounce; confirm
  the message, then backlight off. Keep holding briefly: it may remain energized;
  release PWR and verify battery power actually turns off.
- USB-connected: repeat the hold; confirm the screen goes dark and SYS_EN is LOW
  with a meter, even though USB keeps the MCU supplied. Battery logs must stop.
  Keep holding the original PWR press: no restart. Release, then freshly press
  PWR for at least 30 ms: one clean boot and restored Ready screen/logging.
- After USB wake, keep PWR held beyond 3 seconds: boot protection must prevent
  shutdown until release. Release, then verify a new 3-second hold works again.
- USB standby with battery attached: disconnect USB with PWR released; verify
  the board stays off on battery. A subsequent PWR press must cold-boot normally.
- USB standby without battery: disconnect USB to remove all supply, then reconnect;
  automatic startup must work. Repeat multiple USB on/off and PWR cycles.
- RESET with a partial hold: verify the previous hold duration is discarded.

## Button logic tests

The native test harness compiles and executes the production C state machine
with synthetic timestamps; it does not mock it with a separate Python algorithm.
It checks boot-held PWR, debounce, short/repeated presses, the exact hold boundary,
release-at-deadline cancellation, one-shot behavior, reset and long uptime.
Standby tests additionally execute the production power adapter with GPIO and
restart doubles: original-hold suppression, release/press debounce, SYS_EN HIGH
before restart, failed GPIO-write handling and no automatic restart loops. Loss
of supply is represented only by stopping execution; actual power removal must
be verified on hardware.
Use a native host compiler (Espressif's Xtensa compiler is not a host compiler):

```sh
python tests/check_power_button.py --cc clang
# Or use Zig's host C compiler:
python tests/check_power_button.py --cc /path/to/zig --zig
```

On Windows, standalone clang requires lld; the harness uses a CRT-free DLL.
Zig supplies its own host compiler/linker. Linux/macOS use a normal shared-library
build. Tests use a disposable temporary directory (`--temp-dir DIR` can select
its parent). Neither compiler is a firmware runtime or build dependency.

## Manufacturer and SDK evidence

- [Exact non-touch board](https://docs.waveshare.com/ESP32-S3-LCD-1.69)
- [V2 schematic](https://files.waveshare.com/wiki/ESP32-S3-LCD-1.69/ESP32-S3-LCD-1.69_V2.pdf)
- [ESP-IDF examples](https://docs.waveshare.com/ESP32/ESP32-S3/ESP32-S3-LCD-1.69/ESP32-S3-LCD-1.69/ESP-IDF)
- [Manufacturer source ZIP](https://files.waveshare.com/wiki/ESP32-S3-LCD-1.69/ESP32-S3-LCD-1.69_DemoCode.zip)
- [ESP-IDF LCD documentation](https://docs.espressif.com/projects/esp-idf/en/stable/esp32s3/api-reference/peripherals/lcd.html)

Inspected ZIP: `ESP-IDF-v5.3.1/02_ESP_IDF_ST7789_LVGL/main/main.c`
for native esp_lcd electrical settings, mirror, gap and inversion; and
`01_ESP_IDF_ST7789/components/st7789/st7789.c` for reset/init context.
Only the board settings are used; LVGL is not included. Reset and controller
initialization use the installed v6.1 native ST7789 driver, as the manufacturer's
native example does. The V2 schematic confirms pins and resistor divider.

Future networking can be added behind a separate component; `power`, `battery`
and `display` have narrow interfaces and do not depend on the Python host.
