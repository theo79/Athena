set(screen_c "${CMAKE_CURRENT_BINARY_DIR}/startup_screen.c")
idf_component_register(SRCS "main.c" "power.c" "power_button.c" "battery.c" "display.c" "${screen_c}"
    INCLUDE_DIRS "."
    PRIV_REQUIRES esp_driver_gpio esp_driver_spi esp_lcd esp_adc esp_psram esp_timer esp_system)
add_custom_command(OUTPUT "${screen_c}"
    COMMAND "${PYTHON}" "${PROJECT_DIR}/tools/convert_logo.py"
        "${PROJECT_DIR}/assets/athena-icon.png" "${screen_c}"
    DEPENDS "${PROJECT_DIR}/assets/athena-icon.png"
        "${PROJECT_DIR}/tools/convert_logo.py"
    VERBATIM)
