#include "tunnel/manager.h"

namespace vpn {

class UdpTransport : public Transport {
public:
    void Send(const char* data) override {}
};

}  // namespace vpn
