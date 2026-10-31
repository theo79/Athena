#include "battery.h"
#include "esp_adc/adc_oneshot.h"
#include "esp_adc/adc_cali.h"
#include "esp_adc/adc_cali_scheme.h"
#include "esp_check.h"

static adc_oneshot_unit_handle_t unit;
static adc_cali_handle_t calibration;
static const char *TAG = "battery";

esp_err_t athena_battery_init(void)
{
    const adc_oneshot_unit_init_cfg_t cfg = {.unit_id = ADC_UNIT_1};
    ESP_RETURN_ON_ERROR(adc_oneshot_new_unit(&cfg, &unit), TAG, "ADC init");
    // ESP32-S3 ADC1 channel 0 is GPIO1. 1/3 of 4.2 V is 1.4 V.
    const adc_oneshot_chan_cfg_t channel = {
        .atten = ADC_ATTEN_DB_12, .bitwidth = ADC_BITWIDTH_DEFAULT,
    };
    ESP_RETURN_ON_ERROR(adc_oneshot_config_channel(unit, ADC_CHANNEL_0, &channel), TAG, "ADC channel");
#if ADC_CALI_SCHEME_CURVE_FITTING_SUPPORTED
    const adc_cali_curve_fitting_config_t cal = {
        .unit_id = ADC_UNIT_1, .chan = ADC_CHANNEL_0,
        .atten = ADC_ATTEN_DB_12, .bitwidth = ADC_BITWIDTH_DEFAULT,
    };
    esp_err_t err = adc_cali_create_scheme_curve_fitting(&cal, &calibration);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "Calibration unavailable (%s); raw readings only", esp_err_to_name(err));
    }
#endif
    return ESP_OK;
}

esp_err_t athena_battery_read(int *raw, int *battery_mv)
{
    int sum = 0;
    for (int i = 0; i < 32; ++i) {
        int sample;
        ESP_RETURN_ON_ERROR(adc_oneshot_read(unit, ADC_CHANNEL_0, &sample), TAG, "ADC read");
        sum += sample;
    }
    *raw = (sum + 16) / 32;
    *battery_mv = -1;
    if (!calibration) return ESP_ERR_NOT_SUPPORTED;
    int pin_mv;
    ESP_RETURN_ON_ERROR(adc_cali_raw_to_voltage(calibration, *raw, &pin_mv), TAG, "ADC calibration");
    *battery_mv = pin_mv * 3; // V2: 200 kohm / 100 kohm divider.
    return ESP_OK;
}

esp_err_t athena_battery_deinit(void)
{
    esp_err_t result = ESP_OK;
#if ADC_CALI_SCHEME_CURVE_FITTING_SUPPORTED
    if (calibration) {
        result = adc_cali_delete_scheme_curve_fitting(calibration);
        calibration = NULL;
    }
#endif
    if (unit) {
        esp_err_t err = adc_oneshot_del_unit(unit);
        unit = NULL;
        if (result == ESP_OK) result = err;
    }
    return result;
}
