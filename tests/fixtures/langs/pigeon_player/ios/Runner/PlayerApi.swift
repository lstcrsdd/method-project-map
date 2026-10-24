import Flutter

/// The iOS implementation of the Pigeon API.
class PlayerApi: PlayerHostApi {
  func connect(server: String) throws -> Bool {
    return !server.isEmpty
  }

  func disconnect() throws {}
}
