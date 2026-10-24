#include <windows.h>
#include "engine/manager.h"
#include "session.h"
#include "util.h"

namespace audio {

std::string BaseManager::Describe() const { return "base"; }

Manager::Manager(ConfigStore* store, Transport* transport) : store_(store), transport_(transport) {}

bool Manager::Start(const std::string& id) {
    Profile profile = store_->Load(id);
    Session session(profile);
    session.Open();
    transport_->Send("hello");
    Log("start");
    Describe();
    util::Format(profile);
    MessageBoxW(nullptr, L"Playing", L"Player", MB_OK);
    return true;
}

void Manager::Log(const char* message) {
    OutputDebugStringA(message);
}

}  // namespace audio
