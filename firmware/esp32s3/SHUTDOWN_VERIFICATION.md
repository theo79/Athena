> Historical bring-up verification record. Physical acceptance subsequently
> passed, as reported by the prototype owner on 31 October 2026. Earlier
> "pending" or "not flashed" statements describe the state at that development
> step. The current milestone status is in the
> [Phase A completion report](../../M3_3_PHASE_A_COMPLETION_REPORT.md).

Current power behavior: see [USB standby and PWR wake verification](STANDBY_VERIFICATION.md).

# PWR shutdown implementation verification

31 October 2026; feature/embodied-agent; firmware **v0.1.0** unchanged.

## Implemented behavior

- First app_main action remains SYS_EN GPIO41 HIGH. No bootloader change.
- GPIO40 SYS_OUT is an active-low input with internal pulls/interrupts disabled.
  The V2 board supplies the external pull-up. Main polls every 10 ms using
  FreeRTOS delay-until (verified CONFIG_FREERTOS_HZ=100), without busy-waiting.
- After boot/reset, require 30 ms of released PWR before arming. Confirm a new
  press for 30 ms, then require 3000 ms continuous hold. Any sampled release
  cancels the timer immediately; short presses never accumulate hold time.
- Trigger once: stop normal battery work, upload Shutting down..., wait 750 ms
  for visibility, release ADC calibration/unit, turn GPIO15 backlight off,
  turn LCD off/sleep, delete panel, drain/delete IO, free SPI bus/DMA/semaphore,
  then drive GPIO41 LOW. Cleanup return errors log without an intentional reboot.
- The application remains suspended if button-held or USB power keeps it alive.
  It neither reasserts SYS_EN nor resumes battery sampling after shutdown.

The original PNG and all 134,400 startup RGB565 bytes are unchanged. The upright
portrait mirror(false,false), no XY swap, gap (0,20), RGB order, inversion, colors
and layout remain unchanged. A new 240 x 40 bottom status strip contains the
shutdown message (19,200 flash bytes), generated at build time. It overwrites
the lower version/hardware/status area only during shutdown. Its actual generated
PNG preview was visually inspected. One 9,600-byte DMA stripe is now retained
through normal operation and freed during shutdown after IO has drained.

## Official V2 circuit review

Reviewed the rendered [manufacturer V2 schematic](https://files.waveshare.com/wiki/ESP32-S3-LCD-1.69/ESP32-S3-LCD-1.69_V2.pdf),
especially KEYS and power-supply blocks: GPIO40 maps SYS_OUT; GPIO41 maps SYS_EN.
R20 (10 kohm) pulls SYS_OUT to 3.3 V; Key2 pulls it low through D2. SYS_EN drives
T2/Q5 to hold battery power; the button also has a hardware path through D3.
Consequently power can remain on while PWR is held even after SYS_EN goes LOW.
Release PWR to finish battery-only shutdown. USB can independently keep the
board energized; software cannot remove USB supply using SYS_EN.

## Build and artifact checks

PASS: `idf.py build` using installed ESP-IDF **v6.1** on Windows. No firmware
compiler/linker warnings. Application: **358,208 bytes (0x57740)**;
**1,738,944 bytes free** in the unchanged 2 MiB factory partition,
approximately 83%. Current application SHA256: `105cb7a2f32bc67651813497d99e83de49abf584fb5a071892b2d580f5c94865`.

Offline esptool image-info validates executable checksums/SHA256 hashes.
Partition-table MD5, offsets, sizes and 16 MB limit passed. Bootloader is 21,168
bytes at 0x0; table is 3,072 bytes at 0x8000; application remains at 0x10000.
Flash stays DIO/80 MHz/16 MB, octal PSRAM stays at 80 MHz, native USB Serial/JTAG
console stays enabled. Firmware version, original PNG, board pins, partition
source and sdkconfig.defaults were checked against pre-change SHA256 snapshots.
No Wi-Fi, WebSocket, LVGL, Arduino, provisioning or audio was added. esp_timer
is now a direct dependency for monotonic timing (it was already transitive).

## Executed tests

PASS: compiled and executed the production `main/power_button.c` with the C
scenario harness, warnings treated as errors. Scenarios cover boot-held/stuck-low
PWR, bouncing release and press, exact debounce/hold boundaries, repeated short
presses, release at the deadline, one-shot behavior, reset mid-hold, and uptime
beyond 32-bit milliseconds. These execute C code, not a Python reimplementation.
Native test compiler: Zig 0.15.2 host C compiler, downloaded from the official
download index and SHA256-verified, stored only in the scratch workspace.
Espressif's embedded-only clang could not target x86 Windows; the native test
compiler is not added to the firmware project or runtime dependencies.

```powershell
python tests/check_power_button.py --cc C:\Users\theoc\Documents\ChatGPT\Athena\m33-test-toolchain\zig-x86_64-windows-0.15.2\zig.exe --zig --temp-dir C:\Users\theoc\Documents\ChatGPT\Athena
```

PASS: host regression suite **969 passed, 1 skipped**, 86.64 seconds. Existing
Google genai Python deprecation warning only. Host `python -m pip check` passed.
No tracked host M3.2 files were modified. Whitespace checks passed on new/changed
text; `git -c core.whitespace=cr-at-eol diff --check` passed. Windows ran; Linux
and physical ESP32-S3 button/shutdown tests did not run in this session.

## Modified files for this task

- `README.md`
- `main/CMakeLists.txt`
- `main/battery.c`
- `main/battery.h`
- `main/display.c`
- `main/display.h`
- `main/main.c`
- `main/power.c`
- `main/power.h`
- `tools/convert_logo.py`

New files:

- `main/power_button.c`
- `main/power_button.h`
- `tests/check_power_button.py`
- `tests/power_button_cases.c`
- `SHUTDOWN_VERIFICATION.md` (this report)
- `BUILD_VERIFICATION.md` receives a link to this latest verification.

## Physical acceptance checks after a separately authorized flash

1. Battery boot while holding PWR beyond 3 seconds: stay Ready; release to arm.
2. Short/repeated presses: stay Ready and continue the 10-second battery log.
3. Fresh hold: after 3 seconds plus debounce, see Shutting down... for 750 ms,
   then backlight off. If still holding PWR, release and confirm battery power-off.
4. Repeat on USB: verify SYS_EN LOW and backlight off; MCU may remain supplied,
   but no battery logs or reboot occur. Reset explicitly to run again.
5. Reset while partially or fully holding PWR: require release before any new
   shutdown timing. Check that startup logo/version/orientation are unchanged.
6. Confirm cold battery boot and USB-to-battery handover still work.

This build has **not been flashed**. Physical timing, switch-voltage thresholds,
actual shutdown message and battery power removal remain hardware acceptance
items. No commit, push, whole-flash erase or eFuse operation was performed.
