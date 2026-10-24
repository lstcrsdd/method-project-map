#pragma once
#include "engine/config.h"

namespace audio {

class Session {
public:
    explicit Session(const Profile& profile);
    void Open();
};

}  // namespace audio
