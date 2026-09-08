// The rule for remembering networks, tested without NVS or ESP-IDF.
//
// Kiki moves: she lives at home and travels to college. One remembered network
// meant every arrival was either a re-pick on the panel or a 20-second join
// timeout for an access point that was nowhere near. The list fixes that, but
// only if promotion is exactly right -- a de-duplication bug silently drops the
// network you are standing next to, and you find out when the board sits on the
// provisioning screen somewhere with no cable.
//
// Build and run on the host:
//   g++ -std=c++17 -o /tmp/test_known_networks test_known_networks.cpp
//   /tmp/test_known_networks

#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

namespace {

constexpr int kKnownNetworks = 4;
int failures = 0;

void check(bool ok, const char *what) {
    if (!ok) {
        std::printf("FAIL %s\n", what);
        ++failures;
    }
}

// Mirrors wifi_station_remember(): promote to the front, de-duplicated by
// SSID, oldest falling off the end.
std::vector<std::string> remember(std::vector<std::string> list,
                                  const std::string &ssid) {
    std::vector<std::string> out;
    out.push_back(ssid);
    for (const auto &existing : list) {
        if (static_cast<int>(out.size()) >= kKnownNetworks) break;
        if (existing == ssid) continue;
        out.push_back(existing);
    }
    return out;
}

std::string joined(const std::vector<std::string> &list) {
    std::string text;
    for (const auto &item : list) {
        if (!text.empty()) text += ",";
        text += item;
    }
    return text;
}

}  // namespace

int main() {
    std::vector<std::string> list;

    list = remember(list, "HomeNet");
    check(joined(list) == "HomeNet", "the first network is remembered");

    list = remember(list, "Kiki");
    check(joined(list) == "Kiki,HomeNet", "the newest network goes first");

    // Coming home. HomeNet must move back to the front, and must NOT appear
    // twice -- a duplicate wastes a slot and eventually evicts a real network.
    list = remember(list, "HomeNet");
    check(joined(list) == "HomeNet,Kiki", "rejoining an known network promotes it");

    list = remember(list, "College");
    list = remember(list, "Hotspot");
    check(joined(list) == "Hotspot,College,HomeNet,Kiki", "four networks fit");

    // The fifth evicts the oldest, not the newest.
    list = remember(list, "Cafe");
    check(joined(list) == "Cafe,Hotspot,College,HomeNet", "the oldest falls off");
    check(static_cast<int>(list.size()) == kKnownNetworks, "the list stays bounded");

    // Re-joining the one at the end rescues it from eviction.
    list = remember(list, "HomeNet");
    check(joined(list) == "HomeNet,Cafe,Hotspot,College",
          "the network you are actually on cannot be evicted");

    // Promoting the network already at the front must not duplicate or drop.
    list = remember(list, "HomeNet");
    check(joined(list) == "HomeNet,Cafe,Hotspot,College",
          "re-remembering the current network changes nothing");

    if (failures == 0) {
        std::printf("all known-network checks passed\n");
        return 0;
    }
    std::printf("%d failure(s)\n", failures);
    return 1;
}
