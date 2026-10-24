#include "engine/config.h"

namespace audio {

Profile ConfigStore::Load(const std::string& id) {
    return Profile{id};
}

}  // namespace audio
