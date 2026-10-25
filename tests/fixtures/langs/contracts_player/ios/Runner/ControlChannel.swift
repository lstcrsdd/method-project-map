import Flutter

/// The iOS side of the `player/control` channel.
final class ControlChannel: NSObject {
  private let core = CoreBridge()

  static func register(with messenger: FlutterBinaryMessenger) {
    let channel = FlutterMethodChannel(name: "player/control", binaryMessenger: messenger)
    let instance = ControlChannel()
    channel.setMethodCallHandler(instance.handle)
  }

  func handle(_ call: FlutterMethodCall, result: @escaping FlutterResult) {
    switch call.method {
    case "play":
      result(core.open(path: "current"))
    case "pause":
      result(nil)
    case "seek":
      let args = call.arguments as? [String: Any]
      core.seek(to: (args?["ms"] as? NSNumber)?.int64Value ?? 0)
      result(nil)
    case "release":
      core.close()
      result(nil)
    default:
      result(FlutterMethodNotImplemented)
    }
  }
}
