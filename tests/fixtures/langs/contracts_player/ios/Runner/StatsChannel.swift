import Flutter

/// Streams playback statistics to Dart.
final class StatsChannel {
  static func register(with messenger: FlutterBinaryMessenger) {
    let position = FlutterEventChannel(name: "player/position", binaryMessenger: messenger)
    position.setStreamHandler(PositionStream())
    let level = FlutterEventChannel(name: "player/level", binaryMessenger: messenger)
    level.setStreamHandler(LevelStream())
  }
}

final class PositionStream: NSObject, FlutterStreamHandler {
  func onListen(withArguments arguments: Any?, eventSink events: @escaping FlutterEventSink) -> FlutterError? {
    events(0)
    return nil
  }

  func onCancel(withArguments arguments: Any?) -> FlutterError? {
    return nil
  }
}

final class LevelStream: NSObject, FlutterStreamHandler {
  func onListen(withArguments arguments: Any?, eventSink events: @escaping FlutterEventSink) -> FlutterError? {
    events(0.0)
    return nil
  }

  func onCancel(withArguments arguments: Any?) -> FlutterError? {
    return nil
  }
}
