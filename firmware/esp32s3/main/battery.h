#pragma once
#include "esp_err.h"
esp_err_t athena_battery_init(void);
esp_err_t athena_battery_read(int *raw, int *battery_mv);
esp_err_t athena_battery_deinit(void);
