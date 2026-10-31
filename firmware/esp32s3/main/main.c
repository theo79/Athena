#include "power.h"
#include "power_button.h"
#include "display.h"
#include "battery.h"
#include "esp_log.h"
#include "esp_psram.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static void shutdown_check(esp_err_t err, const char *operation)
{
    // Cleanup errors must not reset the chip and reassert the power latch.
    if (err != ESP_OK) ESP_LOGW("athena", "%s: %s", operation, esp_err_to_name(err));
}

static void standby(void)
{
    athena_standby_button_t button;
    uint64_t now_ms = (uint64_t)esp_timer_get_time() / 1000;
    athena_standby_button_init(&button, athena_power_button_pressed(), now_ms);
    // Always block at least one tick, even if the configured tick rate changes.
    const TickType_t poll_ticks = pdMS_TO_TICKS(10) > 0 ? pdMS_TO_TICKS(10) : 1;
    for (;;) {
        vTaskDelay(poll_ticks);
        now_ms = (uint64_t)esp_timer_get_time() / 1000;
        if (athena_standby_button_update(&button, athena_power_button_pressed(), now_ms)) {
            // esp_restart starts app_main cleanly; no peripheral init here.
            shutdown_check(athena_power_wake(), "SYS_EN wake");
        }
    }
}

static void shutdown(void)
{
    ESP_LOGI("athena", "Shutting down... Release PWR to complete battery power-off; USB may keep power on.");
    shutdown_check(athena_display_shutdown_message(), "Shutdown message");
    vTaskDelay(pdMS_TO_TICKS(750)); // Allow the completed message to be seen.
    shutdown_check(athena_battery_deinit(), "ADC cleanup");
    shutdown_check(athena_display_deinit(), "LCD cleanup"); // Backlight off first.
    shutdown_check(athena_power_release(), "SYS_EN release");
    // Battery-only execution ends when PWR is released. If USB keeps power on,
    // keep only debounced PWR detection active; SYS_EN remains LOW until wake.
    standby();
}

void app_main(void)
{
    athena_power_init(); // First application action, before logging or LCD delays.
    ESP_LOGI("athena", "ATHENA Firmware v0.1.0 | Hardware: ESP32-S3");
    ESP_LOGI("athena", "SYS_EN held HIGH; PSRAM: %u bytes", (unsigned)esp_psram_get_size());
    ESP_ERROR_CHECK(athena_display_startup());
    ESP_ERROR_CHECK(athena_battery_init());
    ESP_LOGI("athena", "Status: Ready");
    athena_power_button_t button;
    uint64_t now_ms = (uint64_t)esp_timer_get_time() / 1000;
    athena_power_button_init(&button, athena_power_button_pressed(), now_ms);
    uint64_t next_battery_ms = now_ms;
    TickType_t last_wake = xTaskGetTickCount();
    while (true) {
        now_ms = (uint64_t)esp_timer_get_time() / 1000;
        if (athena_power_button_update(&button, athena_power_button_pressed(), now_ms)) shutdown();
        if (now_ms >= next_battery_ms) {
            int raw = 0, mv = -1;
            esp_err_t err = athena_battery_read(&raw, &mv);
            if (err == ESP_OK) ESP_LOGI("battery", "Battery divider voltage: %d mV (raw %d)", mv, raw);
            else if (err == ESP_ERR_NOT_SUPPORTED) ESP_LOGW("battery", "Uncalibrated raw ADC: %d", raw);
            else ESP_LOGW("battery", "Read failed: %s", esp_err_to_name(err));
            next_battery_ms = now_ms + 10000;
        }
        vTaskDelayUntil(&last_wake, pdMS_TO_TICKS(10));
    }
}
