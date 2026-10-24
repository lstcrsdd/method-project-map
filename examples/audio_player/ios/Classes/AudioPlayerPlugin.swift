import AVFoundation
import Flutter

/// The iOS side of the `audio/player` channel: one AVPlayer per plugin instance.
public class AudioPlayerPlugin: NSObject, FlutterPlugin {
  private let channel: FlutterMethodChannel
  private var player: AVPlayer?
  private var endObserver: NSObjectProtocol?
  private var volume: Float = 1.0
  private var speed: Float = 1.0

  init(channel: FlutterMethodChannel) {
    self.channel = channel
    super.init()
  }

  public static func register(with registrar: FlutterPluginRegistrar) {
    let channel = FlutterMethodChannel(name: "audio/player", binaryMessenger: registrar.messenger())
    let instance = AudioPlayerPlugin(channel: channel)
    registrar.addMethodCallDelegate(instance, channel: channel)
  }

  public func detachFromEngine(for registrar: FlutterPluginRegistrar) {
    unload()
  }

  /// Replaces the current item and watches for its end, so that Dart learns when playback completes.
  private func load(_ url: URL) {
    unload()
    let item = AVPlayerItem(url: url)
    endObserver = NotificationCenter.default.addObserver(
      forName: .AVPlayerItemDidPlayToEndTime, object: item, queue: .main
    ) { [weak self] _ in
      self?.completed()
    }
    if let player = player {
      player.replaceCurrentItem(with: item)
    } else {
      player = AVPlayer(playerItem: item)
    }
    player?.volume = volume
  }

  private func unload() {
    if let observer = endObserver {
      NotificationCenter.default.removeObserver(observer)
      endObserver = nil
    }
    player?.replaceCurrentItem(with: nil)
  }

  private func activateSession() throws {
    let session = AVAudioSession.sharedInstance()
    try session.setCategory(.playback, mode: .default)
    try session.setActive(true)
  }

  private func completed() {
    channel.invokeMethod("onComplete", arguments: nil)
  }

  private func positionMs() -> Int {
    guard let time = player?.currentTime(), time.isValid else { return 0 }
    return Int(CMTimeGetSeconds(time) * 1000)
  }

  public func handle(_ call: FlutterMethodCall, result: @escaping FlutterResult) {
    let args = call.arguments as? [String: Any] ?? [:]
    switch call.method {
    case "play":
      guard let string = args["url"] as? String, let url = URL(string: string) else {
        result(FlutterError(code: "bad_url", message: "play needs a url", details: nil))
        return
      }
      do {
        try activateSession()
      } catch {
        result(FlutterError(code: "session", message: error.localizedDescription, details: nil))
        return
      }
      volume = Float(args["volume"] as? Double ?? 1.0)
      load(url)
      player?.playImmediately(atRate: speed)
      result(nil)
    case "resume":
      player?.playImmediately(atRate: speed)
      result(nil)
    case "pause":
      player?.pause()
      result(nil)
    case "stop":
      unload()
      result(nil)
    case "seek":
      let ms = args["ms"] as? Int ?? 0
      player?.seek(to: CMTime(value: CMTimeValue(ms), timescale: 1000))
      result(nil)
    case "setVolume":
      volume = Float(args["volume"] as? Double ?? 1.0)
      player?.volume = volume
      result(nil)
    case "setSpeed":
      speed = Float(args["speed"] as? Double ?? 1.0)
      if let player = player, player.rate > 0 {
        player.rate = speed
      }
      result(nil)
    case "position":
      result(positionMs())
    default:
      result(FlutterMethodNotImplemented)
    }
  }
}
