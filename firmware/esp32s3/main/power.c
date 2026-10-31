#include "power.h"
#include "board.h"
#include "esp_err.h"
#include "esp_system.h"

void athena_power_init(void)
{
    // Preload the output latch before enabling the driver; hold SYS_EN until an explicit shutdown.
    ESP_ERROR_CHECK(gpio_set_level(ATHENA_SYS_EN, 1));
    ESP_ERROR_CHECK(gpio_set_direction(ATHENA_SYS_EN, GPIO_MODE_OUTPUT));
    const gpio_config_t button = {
        .pin_bit_mask = 1ULL << ATHENA_SYS_OUT,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    ESP_ERROR_CHECK(gpio_config(&button));
}

bool athena_power_button_pressed(void)
{
    // V2: R20 pulls SYS_OUT HIGH; PWR/Key2 through D2 pulls it LOW.
    return gpio_get_level(ATHENA_SYS_OUT) == 0;
}

esp_err_t athena_power_release(void)
{
    // PWR held down and/or USB may still energize the board after this.
    return gpio_set_level(ATHENA_SYS_EN, 0);
}

esp_err_t athena_power_wake(void)
{
    // Raise battery hold before restarting; never restart if this write fails.
    esp_err_t err = gpio_set_level(ATHENA_SYS_EN, 1);
    if (err == ESP_OK) esp_restart();
    return err;
}
