#pragma once
#include <string>

namespace audio {

/// A playback profile.
struct Profile {
    std::string id;
};

/// Loads profiles.
class ConfigStore {
public:
    Profile Load(const std::string& id);
};

}  // namespace audio
