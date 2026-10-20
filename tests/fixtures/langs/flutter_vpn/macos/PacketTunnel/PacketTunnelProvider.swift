import NetworkExtension

/// The packet tunnel extension: macOS runs it when the app starts the VPN.
class PacketTunnelProvider: NEPacketTunnelProvider {
  override func startTunnel(options: [String: NSObject]?, completionHandler: @escaping (Error?) -> Void) {
    let server = options?["server"] as? String ?? ""
    let settings = NEPacketTunnelNetworkSettings(tunnelRemoteAddress: server)
    setTunnelNetworkSettings(settings) { error in completionHandler(error) }
  }

  override func stopTunnel(with reason: NEProviderStopReason, completionHandler: @escaping () -> Void) {
    completionHandler()
  }

  override func handleAppMessage(_ messageData: Data, completionHandler: ((Data?) -> Void)?) {
    completionHandler?(messageData)
  }
}
