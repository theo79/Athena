#include "power_button.h"

void athena_power_button_init(athena_power_button_t *button, bool pressed, uint64_t now_ms)
{
    button->candidate_since_ms = now_ms;
    button->hold_since_ms = now_ms;
    button->candidate_pressed = pressed;
    button->armed = false; // Boot/reset hold must be released before arming.
    button->tracking = false;
    button->fired = false;
}

bool athena_power_button_update(athena_power_button_t *button, bool pressed, uint64_t now_ms)
{
    if (button->fired) return false;
    if (pressed != button->candidate_pressed) {
        button->candidate_pressed = pressed;
        button->candidate_since_ms = now_ms;
    }
    // Any sampled release breaks continuity, including one near the deadline.
    // A release must remain stable for 30 ms to arm after boot/reset.
    if (!pressed) button->tracking = false;
    if (now_ms - button->candidate_since_ms < ATHENA_BUTTON_DEBOUNCE_MS) return false;
    if (!pressed) {
        button->armed = true;
        return false;
    }
    if (!button->armed) return false;
    if (!button->tracking) {
        button->tracking = true;
        button->hold_since_ms = now_ms;
    }
    if (now_ms - button->hold_since_ms < ATHENA_BUTTON_HOLD_MS) return false;
    button->fired = true;
    return true;
}

void athena_standby_button_init(athena_standby_button_t *button, bool pressed, uint64_t now_ms)
{
    button->candidate_since_ms = now_ms;
    button->candidate_pressed = pressed;
    button->armed = false; // Never reuse the shutdown press as a wake request.
    button->fired = false;
}

bool athena_standby_button_update(athena_standby_button_t *button, bool pressed, uint64_t now_ms)
{
    if (button->fired) return false;
    if (pressed != button->candidate_pressed) {
        button->candidate_pressed = pressed;
        button->candidate_since_ms = now_ms;
    }
    if (now_ms - button->candidate_since_ms < ATHENA_BUTTON_DEBOUNCE_MS) return false;
    if (!pressed) {
        button->armed = true;
        return false;
    }
    if (!button->armed) return false;
    button->fired = true; // Latch before the hardware action; no restart retries.
    return true;
}
