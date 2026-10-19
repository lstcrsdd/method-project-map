#pragma once
#include <string>

namespace vpn {

/// A VPN profile.
struct Profile {
    std::string id;
};

/// Loads profiles.
class ConfigStore {
public:
    Profile Load(const std::string& id);
};

}  // namespace vpn
