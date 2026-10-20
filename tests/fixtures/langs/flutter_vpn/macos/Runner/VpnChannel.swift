import FlutterMacOS
import NetworkExtension

/// The macOS side of the `app.vpn/control` channel.
final class VpnChannel: NSObject {
  static let controlName = "app.vpn/control"
  private let channel: FlutterMethodChannel
  private let tunnel = TunnelController()

  init(channel: FlutterMethodChannel) {
    self.channel = channel
  }

  static func register(with messenger: FlutterBinaryMessenger) {
    let channel = FlutterMethodChannel(name: controlName, binaryMessenger: messenger)
    let instance = VpnChannel(channel: channel)
    channel.setMethodCallHandler(instance.handle)
    let states = FlutterEventChannel(name: "app.vpn/state", binaryMessenger: messenger)
    states.setStreamHandler(StateStreamHandler())
  }

  func handle(_ call: FlutterMethodCall, result: @escaping FlutterResult) {
    switch call.method {
    case "connect":
      let args = call.arguments as? [String: Any]
      tunnel.connect(server: args?["server"] as? String ?? "") { ok in result(ok) }
    case "disconnect":
      tunnel.disconnect()
      result(nil)
    case "status":
      result(tunnel.status())
    default:
      result(FlutterMethodNotImplemented)
    }
  }

  func revoked() {
    channel.invokeMethod("onRevoked", arguments: nil)
  }
}

/// Streams tunnel states to Dart.
final class StateStreamHandler: NSObject, FlutterStreamHandler {
  func onListen(withArguments arguments: Any?, eventSink events: @escaping FlutterEventSink) -> FlutterError? {
    events("disconnected")
    return nil
  }

  func onCancel(withArguments arguments: Any?) -> FlutterError? {
    return nil
  }
}
