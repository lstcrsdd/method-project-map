#pragma once
#include "tunnel/config.h"

namespace vpn {

class Session {
public:
    explicit Session(const Profile& profile);
    void Open();
};

}  // namespace vpn
