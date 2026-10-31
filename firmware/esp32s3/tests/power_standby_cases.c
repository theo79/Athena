#include "power_button.h"
#include "power.h"
#include "board.h"
#include "esp_system.h"

#define CHECK(condition) do { if (!(condition)) return __LINE__; } while (0)
enum { HOLD_LOW = 1, HOLD_HIGH, RESTART, DIRECTION, BUTTON_CONFIG };
static int events[32];
static int event_count;
static int restart_count;
static int sys_en;
static int input_level;
static bool fail_high;

static void record(int event)
{
    if (event_count < 32) events[event_count] = event;
    ++event_count;
}

esp_err_t gpio_set_level(gpio_num_t pin, int level)
{
    if (pin != ATHENA_SYS_EN) return ESP_FAIL;
    record(level ? HOLD_HIGH : HOLD_LOW);
    if (level && fail_high) return ESP_FAIL;
    sys_en = level;
    return ESP_OK;
}

esp_err_t gpio_set_direction(gpio_num_t pin, int mode)
{
    if (pin != ATHENA_SYS_EN || mode != GPIO_MODE_OUTPUT) return ESP_FAIL;
    record(DIRECTION);
    return ESP_OK;
}

esp_err_t gpio_config(const gpio_config_t *config)
{
    if (config->pin_bit_mask != (UINT64_C(1) << ATHENA_SYS_OUT) ||
        config->mode != GPIO_MODE_INPUT || config->pull_up_en != GPIO_PULLUP_DISABLE ||
        config->pull_down_en != GPIO_PULLDOWN_DISABLE || config->intr_type != GPIO_INTR_DISABLE)
        return ESP_FAIL;
    record(BUTTON_CONFIG);
    return ESP_OK;
}

int gpio_get_level(gpio_num_t pin)
{
    return pin == ATHENA_SYS_OUT ? input_level : 1;
}

void esp_restart(void)
{
    record(RESTART);
    ++restart_count;
}

static bool sample(athena_standby_button_t *button, bool pressed, uint64_t now_ms)
{
    if (!athena_standby_button_update(button, pressed, now_ms)) return false;
    (void)athena_power_wake(); // Production adapter, mocked GPIO/restart only.
    return true;
}

#ifdef _WIN32
__declspec(dllexport)
#endif
int run_power_standby_tests(void)
{
    athena_standby_button_t b;
    event_count = 0; restart_count = 0; fail_high = false; sys_en = 0;
    // Preserve first-action hold and the exact V2 input configuration.
    athena_power_init();
    CHECK(event_count == 3 && events[0] == HOLD_HIGH && events[1] == DIRECTION &&
          events[2] == BUTTON_CONFIG && sys_en == 1);
    input_level = 0; CHECK(athena_power_button_pressed());
    input_level = 1; CHECK(!athena_power_button_pressed());

    // USB standby entry releases hold and never restarts on the original press.
    event_count = 0;
    CHECK(athena_power_release() == ESP_OK);
    CHECK(sys_en == 0 && events[0] == HOLD_LOW);
    athena_standby_button_init(&b, true, 0);
    for (uint64_t t = 0; t <= 10000; t += 10) CHECK(!sample(&b, true, t));
    CHECK(!b.armed && restart_count == 0 && event_count == 1 && sys_en == 0);

    // Release bounce cannot arm. A full 30 ms release can; no wake on release.
    CHECK(!sample(&b, false, 10010));
    CHECK(!sample(&b, true, 10020));
    CHECK(!sample(&b, false, 10030));
    CHECK(!sample(&b, false, 10059));
    CHECK(!b.armed);
    CHECK(!sample(&b, false, 10060));
    CHECK(b.armed && restart_count == 0 && sys_en == 0);
    // A fresh press must itself survive debounce, including press bounce.
    CHECK(!sample(&b, true, 10100));
    CHECK(!sample(&b, false, 10110));
    CHECK(!sample(&b, true, 10120));
    CHECK(!sample(&b, true, 10149));
    CHECK(restart_count == 0 && sys_en == 0);
    CHECK(sample(&b, true, 10150));
    CHECK(restart_count == 1 && sys_en == 1 && event_count == 3);
    CHECK(events[1] == HOLD_HIGH && events[2] == RESTART); // Actual adapter order.
    // Even a returning restart double cannot provoke another restart loop.
    for (uint64_t t = 10160; t <= 20000; t += 10) CHECK(!sample(&b, true, t));
    CHECK(!sample(&b, false, 20010)); CHECK(!sample(&b, false, 20040));
    CHECK(!sample(&b, true, 20100)); CHECK(!sample(&b, true, 20130));
    CHECK(restart_count == 1 && event_count == 3);

    // Simulated reboot while wake press stays held: normal boot guard survives.
    athena_power_button_t running;
    athena_power_button_init(&running, true, 0);
    for (uint64_t t = 0; t <= 10000; t += 10)
        CHECK(!athena_power_button_update(&running, true, t));
    CHECK(!running.armed);

    // Shutdown press already released before standby: still require debounce.
    event_count = 0; restart_count = 0;
    CHECK(athena_power_release() == ESP_OK);
    athena_standby_button_init(&b, false, 0);
    CHECK(!sample(&b, false, 29)); CHECK(!b.armed);
    CHECK(!sample(&b, false, 30)); CHECK(b.armed);
    // Idle/no supply-detection activity never raises SYS_EN.
    CHECK(!sample(&b, false, 100000));
    CHECK(event_count == 1 && sys_en == 0 && restart_count == 0);
    // Battery/USB power loss is modeled by stopping all execution here:
    // no scheduled callback exists that can raise SYS_EN after samples cease.
    CHECK(sys_en == 0);

    // Failed hold reassertion must suppress restart and must not retry forever.
    fail_high = true;
    CHECK(!sample(&b, true, 100010));
    CHECK(sample(&b, true, 100040));
    CHECK(restart_count == 0 && sys_en == 0 && events[1] == HOLD_HIGH);
    CHECK(!sample(&b, true, 200000));
    CHECK(event_count == 2);
    fail_high = false;

    // A fresh cold boot has no retained standby state or automatic wake event.
    athena_standby_button_init(&b, true, 0);
    CHECK(!sample(&b, true, 10000));
    CHECK(restart_count == 0);
    // Long uptime does not wrap the release/press debounce arithmetic.
    uint64_t base = UINT64_C(4294967296);
    athena_standby_button_init(&b, false, base);
    CHECK(!sample(&b, false, base + 30));
    CHECK(!sample(&b, true, base + 40));
    CHECK(!sample(&b, true, base + 69));
    CHECK(sample(&b, true, base + 70));
    CHECK(restart_count == 1 && sys_en == 1);
    return 0;
}
