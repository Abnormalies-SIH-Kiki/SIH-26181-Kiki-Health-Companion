#include "kiki_fall_detector.hpp"
#include <cassert>
#include <cstdio>

int main() {
    kiki::FallDetector d;
    for (unsigned t = 0; t < 1000; t += 20)
        assert(!d.update(t, (t % 100 == 0) ? 1.8F : 1.0F, true));
    assert(!d.update(1000, 3.0F, true)); // impact alone is not a fall
    for (unsigned t = 1100; t <= 1200; t += 20) assert(!d.update(t, .3F, true));
    assert(d.update(1220, 3.0F, false)); // transient optical loss at impact
    for (unsigned t = 1300; t < 1800; t += 20) assert(!d.update(t, .3F, true));
    assert(!d.update(1820, 3.0F, true)); // no duplicate episode
    kiki::FallDetector off;
    for (unsigned t = 0; t < 200; t += 20) assert(!off.update(t, .2F, false));
    assert(!off.update(220, 4.0F, false)); // unworn device drop
    kiki::FallDetector stale;
    stale.update(0, 1.F, true);
    for (unsigned t = 31000; t < 31200; t += 20) assert(!stale.update(t, .2F, false));
    assert(!stale.update(31220, 4.0F, false));
    kiki::FallDetector late;
    for (unsigned t = 0; t < 200; t += 20) late.update(t, .2F, true);
    assert(!late.update(2000, 4.F, true));
    puts("fall detector checks passed");
}
