#include "power_button.h"

#ifdef _WIN32
// Minimal DLL loader hook; the scenarios need no C runtime initialization.
int __stdcall _DllMainCRTStartup(void *module, unsigned long reason, void *reserved)
{
    (void)module; (void)reason; (void)reserved;
    return 1;
}
#endif

#define CHECK(condition) do { if (!(condition)) return __LINE__; } while (0)
#ifdef _WIN32
__declspec(dllexport)
#endif
int run_power_button_tests(void)
{
    athena_power_button_t b;
    // A boot-held or stuck-low PWR cannot arm, regardless of duration.
    athena_power_button_init(&b, true, 0);
    for (uint64_t t = 0; t <= 10000; t += 10) {
        CHECK(!athena_power_button_update(&b, true, t));
    }
    CHECK(!b.armed);
    // A bouncing initial release does not arm; a stable release does.
    CHECK(!athena_power_button_update(&b, false, 10010));
    CHECK(!athena_power_button_update(&b, true, 10020));
    CHECK(!athena_power_button_update(&b, false, 10030));
    CHECK(!athena_power_button_update(&b, false, 10059));
    CHECK(!b.armed);
    CHECK(!athena_power_button_update(&b, false, 10060));
    CHECK(b.armed);
    // Ignore press bounce and count a full 3000 ms only after confirmation.
    CHECK(!athena_power_button_update(&b, true, 10100));
    CHECK(!athena_power_button_update(&b, false, 10110));
    CHECK(!athena_power_button_update(&b, true, 10120));
    CHECK(!athena_power_button_update(&b, true, 10149));
    CHECK(!b.tracking);
    CHECK(!athena_power_button_update(&b, true, 10150));
    CHECK(b.tracking);
    CHECK(!athena_power_button_update(&b, true, 13149));
    CHECK(athena_power_button_update(&b, true, 13150));
    // Exactly one shutdown event, even if held or re-pressed under USB power.
    CHECK(!athena_power_button_update(&b, true, 20000));
    CHECK(!athena_power_button_update(&b, false, 20010));
    CHECK(!athena_power_button_update(&b, false, 20040));
    CHECK(!athena_power_button_update(&b, true, 21000));
    CHECK(!athena_power_button_update(&b, true, 25000));
    // Repeated short presses never accumulate time.
    athena_power_button_init(&b, false, 0);
    CHECK(!athena_power_button_update(&b, false, 30));
    for (uint64_t t = 100; t < 20000; t += 2000) {
        CHECK(!athena_power_button_update(&b, true, t));
        CHECK(!athena_power_button_update(&b, true, t + 30));
        CHECK(!athena_power_button_update(&b, true, t + 1800));
        CHECK(!athena_power_button_update(&b, false, t + 1900));
        CHECK(!athena_power_button_update(&b, false, t + 1930));
    }
    // A release at the deadline cancels, even before release debounce expires.
    athena_power_button_init(&b, false, 0);
    CHECK(!athena_power_button_update(&b, false, 30));
    CHECK(!athena_power_button_update(&b, true, 100));
    CHECK(!athena_power_button_update(&b, true, 130));
    CHECK(!athena_power_button_update(&b, true, 3129));
    CHECK(!athena_power_button_update(&b, false, 3130));
    CHECK(!athena_power_button_update(&b, true, 3140));
    CHECK(!athena_power_button_update(&b, true, 3170));
    CHECK(!athena_power_button_update(&b, true, 6169));
    CHECK(athena_power_button_update(&b, true, 6170));
    // Reset during a nearly-complete hold loses all timing and requires release.
    athena_power_button_init(&b, false, 0);
    CHECK(!athena_power_button_update(&b, false, 30));
    CHECK(!athena_power_button_update(&b, true, 100));
    CHECK(!athena_power_button_update(&b, true, 130));
    CHECK(!athena_power_button_update(&b, true, 3100));
    athena_power_button_init(&b, true, 0);
    CHECK(!athena_power_button_update(&b, true, 10000));
    CHECK(!athena_power_button_update(&b, false, 10010));
    CHECK(!athena_power_button_update(&b, false, 10040));
    CHECK(!athena_power_button_update(&b, true, 10100));
    CHECK(!athena_power_button_update(&b, true, 10130));
    CHECK(athena_power_button_update(&b, true, 13130));
    // Uptime beyond 32-bit milliseconds still gives the same hold duration.
    uint64_t base = UINT64_C(4294967200);
    athena_power_button_init(&b, false, base);
    CHECK(!athena_power_button_update(&b, false, base + 30));
    CHECK(!athena_power_button_update(&b, true, base + 100));
    CHECK(!athena_power_button_update(&b, true, base + 130));
    CHECK(!athena_power_button_update(&b, true, base + 3129));
    CHECK(athena_power_button_update(&b, true, base + 3130));
    return 0;
}
