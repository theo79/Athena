#pragma once
#include <stdbool.h>
#include <stdint.h>

#define ATHENA_BUTTON_DEBOUNCE_MS 30
#define ATHENA_BUTTON_HOLD_MS 3000

typedef struct {
    uint64_t candidate_since_ms;
    uint64_t hold_since_ms;
    bool candidate_pressed;
    bool armed;
    bool tracking;
    bool fired;
} athena_power_button_t;

void athena_power_button_init(athena_power_button_t *button, bool pressed, uint64_t now_ms);
// Monotonic timestamps, sampled regularly. True exactly once per initialization.
bool athena_power_button_update(athena_power_button_t *button, bool pressed, uint64_t now_ms);

// Standby deliberately has no hold timer: release, then a fresh debounced press.
typedef struct {
    uint64_t candidate_since_ms;
    bool candidate_pressed;
    bool armed;
    bool fired;
} athena_standby_button_t;

void athena_standby_button_init(athena_standby_button_t *button, bool pressed, uint64_t now_ms);
bool athena_standby_button_update(athena_standby_button_t *button, bool pressed, uint64_t now_ms);
