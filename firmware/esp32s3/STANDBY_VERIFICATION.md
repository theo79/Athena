> Historical bring-up verification record. Physical acceptance subsequently
> passed, as reported by the prototype owner on 31 October 2026. Earlier
> "pending" or "not flashed" statements describe the state at that development
> step. The current milestone status is in the
> [Phase A completion report](../../M3_3_PHASE_A_COMPLETION_REPORT.md).

# Final PWR management: USB standby and clean wake

31 October 2026; `feature/embodied-agent`; firmware **v0.1.0**; Windows host.
This report supersedes the earlier USB-suspended-until-reset behavior.

## Behavior

Normal operation is unchanged: first app_main action asserts GPIO41 SYS_EN HIGH;
GPIO40 is active LOW; the existing 30 ms debounce, initial released-button guard,
3-second hold and non-accumulating short presses remain intact. The original
normal-operation C state machine was verified byte-for-byte unchanged.

Shutdown preserves the existing sequence: message, 750 ms visibility, ADC cleanup,
LCD/backlight cleanup and SYS_EN LOW. If power persists, the existing main task
enters software standby; no extra task, deep sleep or USB detection is added.

Standby holds SYS_EN LOW and polls only GPIO40 every 10 ms, blocking at least one
tick. Require 30 ms continuously released, then a fresh 30 ms pressed level. The
original shutdown hold cannot wake the board. A one-shot latch is set before
calling the wake adapter. The production adapter sets SYS_EN HIGH, checks success,
then calls esp_restart(). It does not initialize LCD, ADC or other peripherals.
If the GPIO write fails, restart is suppressed; no automatic retry loop occurs.
After clean startup, normal boot protection requires release of the held wake
press before another 3-second shutdown can be recognized.

There are no standby battery reads, LCD redraws or periodic logs. The ESP-IDF
runtime, FreeRTOS idle/system tasks, timer and native USB console still exist;
this is software standby, without a specified deep-sleep current target.

The [V2 schematic](https://files.waveshare.com/wiki/ESP32-S3-LCD-1.69/ESP32-S3-LCD-1.69_V2.pdf)
shows separate button and SYS_EN battery-switch control paths, plus USB supply.
Thus PWR itself can keep battery power on while held. Once released in standby,
USB removal leaves SYS_EN LOW and the battery path off. If all supply disappears,
execution stops naturally; no state is stored. A later battery PWR press uses the
existing cold-boot path. Normal USB connection from power-off still boots.

[ESP-IDF v6.1 restart documentation](https://docs.espressif.com/projects/esp-idf/en/v6.1/esp32s3/api-reference/system/misc_system_api.html#software-reset)
specifies application startup through the bootloader after esp_restart().
No eFuse, security, sleep wake configuration or flash operation is performed.

## Build and artifacts

PASS: ESP-IDF **v6.1**, `idf.py build`; no compiler/linker warnings.
Application: **358,112 bytes (`0x576e0`)**, **1,739,040 bytes free**
in the unchanged 2 MiB app partition (approximately 83%).
Application SHA256: `d679b4795d62a2b381b20b15eb3d4d7675cf12683d00387d8d4c56cd627d04a5`.

Executable checksums/validation hashes, partition-table MD5 and flash layout
passed offline checks. Offsets remain bootloader 0x0, table 0x8000, app 0x10000;
DIO/80 MHz/16 MB flash and octal PSRAM settings are unchanged. esp_system is now
explicitly listed as a direct dependency for restart; it was already transitive.

Original PNG, startup preview, converter, display code/orientation/layout,
battery code, board pin definitions, project version, partitions and defaults
were checked against pre-change hashes and are unchanged. No battery data was
added to the screen. Host M3.2/provider/Wi-Fi files have no tracked diff.

## Native C regression results

PASS: existing `run_power_button_tests` plus new `run_power_standby_tests`, built
with C warnings as errors and executed natively on Windows with Zig 0.15.2.
The tests compile actual main/power_button.c and main/power.c; only ESP-IDF GPIO
and restart are replaced with test doubles. Stubs are confined to tests/stubs
and are not in the firmware component include path.

Verified cases:

- SYS_EN HIGH is the first power-init action; V2 active-low input config.
- USB standby entry releases SYS_EN; a long original press never restarts.
- Release bounce cannot arm; exact 30 ms release threshold arms without waking.
- Fresh-press bounce and exact 30 ms press threshold; short noise does not wake.
- Event trace proves production wake writes GPIO41 HIGH before esp_restart.
- Single restart even if the test restart returns, PWR remains held, or is
  released/re-pressed after the wake event; no repeated restart requests.
- Failed GPIO41 assertion suppresses restart and repeated wake attempts.
- Simulated reboot with held wake button preserves normal boot protection.
- Standby entered with button already released still debounces before arming.
- Idle standby keeps SYS_EN LOW indefinitely; no background action raises it.
- Supply loss is modeled only by ending execution while SYS_EN remains LOW;
  actual battery/USB voltage and physical power removal cannot be tested natively.
- Fresh state after a cold start; correct behavior beyond 32-bit millisecond uptime.

Reproduce from firmware/esp32s3 (use your native compiler path):

```powershell
python tests/check_power_button.py --cc C:\Users\theoc\Documents\ChatGPT\Athena\m33-test-toolchain\zig-x86_64-windows-0.15.2\zig.exe --zig --temp-dir C:\Users\theoc\Documents\ChatGPT\Athena
```

Full host regression: **969 passed, 1 skipped**, 67.15 seconds; one existing
Google genai dependency deprecation warning. Host pip check passed. Whitespace
checks and `git -c core.whitespace=cr-at-eol diff --check` passed.
Windows ran; Linux, hardware standby current, GPIO voltages and physical restart
cycles were not tested in this session.

## Files changed for this correction

- `README.md`
- `main/CMakeLists.txt`
- `main/main.c`
- `main/power.c`
- `main/power.h`
- `main/power_button.c`
- `main/power_button.h`
- `tests/check_power_button.py`

Added:

- `tests/power_standby_cases.c`
- `tests/stubs/driver/gpio.h`
- `tests/stubs/esp_err.h`
- `tests/stubs/esp_system.h`
- `STANDBY_VERIFICATION.md` (this report).
- Earlier verification reports receive links to this current behavior.

## Exact physical acceptance checklist — after separate flash authorization

Use a data USB cable and charged battery as specified. Observe console startup
sequences and, where practical, measure SYS_EN (GPIO41) and backlight (GPIO15).
Wait for Ready before normal-operation tests; standby starts after the shutdown
message and 750 ms visibility interval plus cleanup. For manual debounce tests,
use release/press durations of at least 100 ms; native tests cover exact 30 ms edges.

1. USB automatic boot: start unpowered, connect USB. Confirm one v0.1.0 startup,
   Ready, upright original screen, 8 MB PSRAM and SYS_EN HIGH. Do not press PWR.
2. Normal short presses: tap PWR briefly and repeat. No shutdown; battery logs
   continue at the existing cadence. Normal screen has no battery overlay.
3. USB shutdown: hold PWR at least 3 seconds after debounce. Confirm the exact
   message for 750 ms, then LCD/backlight off and SYS_EN LOW. USB remains attached.
4. Original-hold suppression: keep the shutdown press held for 10 more seconds.
   No restart, no new Ready sequence, no battery logs, no LCD activity.
5. Release-only: release PWR for at least 100 ms. Wait 5 seconds without pressing.
   Remain dark/in standby, SYS_EN LOW, no restart or battery logs.
6. Fresh USB wake: press PWR for at least 100 ms. Confirm SYS_EN HIGH and exactly
   one clean startup/Ready sequence with the same logo/version/layout. No RST or
   USB reconnection should be needed. Keep holding until Ready, then hold another
   5 seconds: boot protection must prevent a second shutdown or restart.
7. Repeatability: release for at least 100 ms; repeat the normal 3-second shutdown
   and fresh wake cycle at least five times. Check one startup per wake, no
   spontaneous cycles and continued normal battery logging only when running.
8. Battery-only regression: disconnect USB while running. Shutdown with a full
   hold; release PWR after the screen goes dark. Confirm the board turns off.
   Fresh PWR press must cold-boot; hold through boot, release after Ready.
9. USB removal during standby: attach battery and USB, shut down, then release
   PWR for at least 100 ms. Disconnect USB without pressing PWR. Verify the board
   remains off on battery. Fresh PWR must cold-boot normally.
10. USB removal with shutdown press still held: repeat shutdown, keep PWR held,
    remove USB. The button may maintain battery power. Release PWR and verify
    power-off without a restart. This is the expected hardware button path.
11. USB-only total power loss: with battery disconnected, enter USB standby.
    Disconnect USB: execution must stop. Reconnect USB: automatic startup works.
12. Transition around wake: with battery attached, wake from USB standby and
    disconnect USB near restart while holding PWR through Ready. Verify normal
    battery operation. Record any latch interruption, reset or brownout.
13. Reset guard: while running, partially hold PWR then press RST while still
    holding PWR through boot. No shutdown until release and a fresh full hold.

Remaining hardware risks: GPIO41 latch continuity across esp_restart and very
early USB removal/PWR release; real switch thresholds/bounce; supply/brownout
behavior during handover; USB enumeration/log continuity after software reset.
Standby does not disconnect USB or promise deep-sleep power consumption.
If SYS_EN cannot be asserted on wake, firmware stays in standby rather than
restarting repeatedly; reset is the recovery path for that exceptional failure.

**Stop point: build verification complete. No flash, commit or push performed.**
