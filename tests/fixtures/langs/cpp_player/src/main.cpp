#include <windows.h>
#include "engine/manager.h"

int main() {
    audio::ConfigStore store;
    audio::Manager manager(&store, nullptr);
    manager.Start("home");
    return 0;
}
