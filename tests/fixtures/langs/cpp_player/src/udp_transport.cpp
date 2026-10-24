#include "engine/manager.h"

namespace audio {

class UdpTransport : public Transport {
public:
    void Send(const char* data) override {}
};

}  // namespace audio
