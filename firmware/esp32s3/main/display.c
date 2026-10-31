#include "display.h"
#include "board.h"
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include "driver/spi_master.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_vendor.h"
#include "esp_lcd_panel_ops.h"
#include "esp_check.h"
#include "esp_heap_caps.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

extern const uint8_t athena_startup_rgb565[240 * 280 * 2];
extern const uint8_t athena_shutdown_rgb565[240 * 40 * 2];
static const char *TAG = "display";
static esp_lcd_panel_io_handle_t io;
static esp_lcd_panel_handle_t panel;
static SemaphoreHandle_t done;
static uint8_t *stripe;

static esp_err_t draw_rows(const uint8_t *pixels, int y_start, int height)
{
    for (int row = 0; row < height; row += 20) {
        memcpy(stripe, pixels + row * 240 * 2, 240 * 20 * 2);
        ESP_RETURN_ON_ERROR(esp_lcd_panel_draw_bitmap(panel, 0, y_start + row,
                            240, y_start + row + 20, stripe), TAG, "draw");
        // DMA storage stays valid through timeout and is freed only after IO drain.
        if (xSemaphoreTake(done, pdMS_TO_TICKS(1000)) != pdTRUE) return ESP_ERR_TIMEOUT;
    }
    return ESP_OK;
}

static bool transfer_done(esp_lcd_panel_io_handle_t io,
                          esp_lcd_panel_io_event_data_t *event, void *context)
{
    (void)io;
    (void)event;
    BaseType_t wake = pdFALSE;
    xSemaphoreGiveFromISR((SemaphoreHandle_t)context, &wake);
    return wake == pdTRUE;
}

esp_err_t athena_display_startup(void)
{
    ESP_RETURN_ON_ERROR(gpio_set_level(ATHENA_LCD_BL, 0), TAG, "backlight off");
    ESP_RETURN_ON_ERROR(gpio_set_direction(ATHENA_LCD_BL, GPIO_MODE_OUTPUT), TAG, "backlight GPIO");
    const spi_bus_config_t bus = {
        .mosi_io_num = ATHENA_LCD_MOSI, .miso_io_num = -1,
        .sclk_io_num = ATHENA_LCD_CLK, .quadwp_io_num = -1, .quadhd_io_num = -1,
        .max_transfer_sz = ATHENA_LCD_WIDTH * 20 * 2,
    };
    ESP_RETURN_ON_ERROR(spi_bus_initialize(SPI3_HOST, &bus, SPI_DMA_CH_AUTO), TAG, "SPI init");
    done = xSemaphoreCreateBinary();
    if (!done) return ESP_ERR_NO_MEM;
    const esp_lcd_panel_io_spi_config_t io_cfg = {
        .dc_gpio_num = ATHENA_LCD_DC, .cs_gpio_num = ATHENA_LCD_CS,
        .pclk_hz = 40000000, .lcd_cmd_bits = 8, .lcd_param_bits = 8,
        .spi_mode = 0, .trans_queue_depth = 1,
        .on_color_trans_done = transfer_done, .user_ctx = done,
    };
    ESP_RETURN_ON_ERROR(esp_lcd_new_panel_io_spi((esp_lcd_spi_bus_handle_t)SPI3_HOST, &io_cfg, &io), TAG, "panel IO");
    const esp_lcd_panel_dev_config_t cfg = {
        .reset_gpio_num = ATHENA_LCD_RST,
        .rgb_ele_order = LCD_RGB_ELEMENT_ORDER_RGB,
        .data_endian = LCD_RGB_DATA_ENDIAN_BIG, .bits_per_pixel = 16,
    };
    ESP_RETURN_ON_ERROR(esp_lcd_new_panel_st7789(io, &cfg, &panel), TAG, "ST7789");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_reset(panel), TAG, "reset");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_init(panel), TAG, "init");
    // Physical V2 mounting: toggle both axes for a 180-degree correction.
    ESP_RETURN_ON_ERROR(esp_lcd_panel_mirror(panel, false, false), TAG, "mirror");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_swap_xy(panel, false), TAG, "portrait");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_set_gap(panel, 0, 20), TAG, "offset");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_invert_color(panel, true), TAG, "inversion");
    stripe = heap_caps_malloc(240 * 20 * 2, MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
    if (!stripe) return ESP_ERR_NO_MEM;
    ESP_RETURN_ON_ERROR(draw_rows(athena_startup_rgb565, 0, 280), TAG, "startup frame");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_disp_on_off(panel, true), TAG, "display on");
    ESP_RETURN_ON_ERROR(gpio_set_level(ATHENA_LCD_BL, 1), TAG, "backlight on");
    // Retain the DMA stripe and panel resources for the shutdown message.
    return ESP_OK;
}

esp_err_t athena_display_shutdown_message(void)
{
    if (!panel || !stripe || !done) return ESP_ERR_INVALID_STATE;
    return draw_rows(athena_shutdown_rgb565, 240, 40);
}

esp_err_t athena_display_deinit(void)
{
    esp_err_t result = gpio_set_level(ATHENA_LCD_BL, 0);
    // Best-effort cleanup: an LCD failure must not prevent SYS_EN release.
#define CLEANUP(call) do { esp_err_t err = (call); \
    if (err != ESP_OK) { ESP_LOGW(TAG, "%s: %s", #call, esp_err_to_name(err)); \
        if (result == ESP_OK) result = err; } } while (0)
    if (panel) {
        CLEANUP(esp_lcd_panel_disp_on_off(panel, false));
        CLEANUP(esp_lcd_panel_disp_sleep(panel, true));
        CLEANUP(esp_lcd_panel_del(panel));
        panel = NULL;
    }
    if (io) {
        // Native SPI IO deletion drains outstanding DMA before freeing storage.
        esp_err_t err = esp_lcd_panel_io_del(io);
        if (err != ESP_OK) {
            ESP_LOGW(TAG, "IO cleanup: %s", esp_err_to_name(err));
            return result == ESP_OK ? err : result;
        }
        io = NULL;
    }
    CLEANUP(spi_bus_free(SPI3_HOST));
    free(stripe);
    stripe = NULL;
    if (done) vSemaphoreDelete(done);
    done = NULL;
#undef CLEANUP
    return result;
}
