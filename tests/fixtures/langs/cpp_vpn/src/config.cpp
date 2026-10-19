#include "tunnel/config.h"

namespace vpn {

Profile ConfigStore::Load(const std::string& id) {
    return Profile{id};
}

}  // namespace vpn
