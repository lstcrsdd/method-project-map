import NetworkExtension

/// Starts and stops the packet tunnel through NetworkExtension; checks the profile with the Rust core first.
final class TunnelController {
  private var manager: NETunnelProviderManager?

  func connect(server: String, completion: @escaping (Bool) -> Void) {
    guard vpn_core_start(server) == 0 else {
      completion(false)
      return
    }
    NETunnelProviderManager.loadAllFromPreferences { managers, _ in
      let manager = managers?.first ?? NETunnelProviderManager()
      self.manager = manager
      do {
        try manager.connection.startVPNTunnel(options: ["server": server as NSString])
        completion(true)
      } catch {
        completion(false)
      }
    }
  }

  func disconnect() {
    manager?.connection.stopVPNTunnel()
    vpn_core_stop()
  }

  func status() -> String {
    return manager?.connection.status == .connected ? "connected" : "disconnected"
  }
}
