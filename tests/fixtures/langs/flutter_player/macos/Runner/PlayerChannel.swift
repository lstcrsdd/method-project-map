import FlutterMacOS
import NetworkExtension

/// The macOS side of the `audio/player` channel.
final class PlayerChannel: NSObject {
  static let controlName = "audio/player"
  private let channel: FlutterMethodChannel
  private let playback = ExtensionController()

  init(channel: FlutterMethodChannel) {
    self.channel = channel
  }

  static func register(with messenger: FlutterBinaryMessenger) {
    let channel = FlutterMethodChannel(name: controlName, binaryMessenger: messenger)
    let instance = PlayerChannel(channel: channel)
    channel.setMethodCallHandler(instance.handle)
    let states = FlutterEventChannel(name: "audio/player/state", binaryMessenger: messenger)
    states.setStreamHandler(StateStreamHandler())
  }

  func handle(_ call: FlutterMethodCall, result: @escaping FlutterResult) {
    switch call.method {
    case "connect":
      let args = call.arguments as? [String: Any]
      playback.connect(server: args?["server"] as? String ?? "") { ok in result(ok) }
    case "disconnect":
      playback.disconnect()
      result(nil)
    case "status":
      result(playback.status())
    default:
      result(FlutterMethodNotImplemented)
    }
  }

  func revoked() {
    channel.invokeMethod("onRevoked", arguments: nil)
  }
}

/// Streams player states to Dart.
final class StateStreamHandler: NSObject, FlutterStreamHandler {
  func onListen(withArguments arguments: Any?, eventSink events: @escaping FlutterEventSink) -> FlutterError? {
    events("disconnected")
    return nil
  }

  func onCancel(withArguments arguments: Any?) -> FlutterError? {
    return nil
  }
}
