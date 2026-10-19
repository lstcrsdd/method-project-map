#include <windows.h>
#include "tunnel/manager.h"

int main() {
    vpn::ConfigStore store;
    vpn::Manager manager(&store, nullptr);
    manager.Start("home");
    return 0;
}
