import NetworkExtension

/// The app's NetworkExtension provider: macOS runs it when the app starts the extension.
class ExtensionProvider: NEPacketTunnelProvider {
  override func startTunnel(options: [String: NSObject]?, completionHandler: @escaping (Error?) -> Void) {
    let server = options?["server"] as? String ?? ""
    let error: Error? = server.isEmpty ? CocoaError(.featureUnsupported) : nil
    completionHandler(error)
  }

  override func stopTunnel(with reason: NEProviderStopReason, completionHandler: @escaping () -> Void) {
    completionHandler()
  }

  override func handleAppMessage(_ messageData: Data, completionHandler: ((Data?) -> Void)?) {
    completionHandler?(messageData)
  }
}
