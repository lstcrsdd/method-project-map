import Foundation

/// Calls into the audio engine through player_core.h (the bridging header).
final class CoreBridge {
  func open(path: String) -> Bool {
    return player_core_open(path) == 0
  }

  func close() {
    player_core_close()
  }

  func seek(to positionMs: Int64) {
    player_core_seek(positionMs, 0)
  }

  func position() -> Int64 {
    return player_core_position()
  }

  func listen(context: UnsafeMutableRawPointer) {
    player_core_on_event(context) { _, code in
      print("engine event \(code)")
    }
  }
}
