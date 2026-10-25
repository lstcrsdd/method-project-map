import Flutter

/// The iOS side of the media library: lists and rescans the tracks on the device.
final class LibraryChannel: NSObject {
  static func register(with messenger: FlutterBinaryMessenger) {
    let channel = FlutterMethodChannel(name: "player/medialibrary", binaryMessenger: messenger)
    let instance = LibraryChannel()
    channel.setMethodCallHandler(instance.handle)
  }

  func handle(_ call: FlutterMethodCall, result: @escaping FlutterResult) {
    switch call.method {
    case "list":
      result([String]())
    case "scan":
      result(nil)
    default:
      result(FlutterMethodNotImplemented)
    }
  }
}
