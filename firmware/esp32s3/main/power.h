#pragma once
#include <stdbool.h>
#include "esp_err.h"
void athena_power_init(void);
bool athena_power_button_pressed(void);
esp_err_t athena_power_release(void);
esp_err_t athena_power_wake(void);
