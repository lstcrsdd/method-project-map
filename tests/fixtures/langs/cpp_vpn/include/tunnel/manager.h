#pragma once
#include <string>
#include "config.h"

namespace vpn {

/// Sends packets somewhere.
class Transport {
public:
    virtual ~Transport() = default;
    virtual void Send(const char* data) = 0;
};

class BaseManager {
public:
    std::string Describe() const;
};

/// Starts and stops the tunnel.
class Manager : public BaseManager {
public:
    Manager(ConfigStore* store, Transport* transport);
    bool Start(const std::string& id);

private:
    void Log(const char* message);
    ConfigStore* store_;
    Transport* transport_;
};

}  // namespace vpn
