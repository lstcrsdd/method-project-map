import Flutter

/// The iOS implementation of the Pigeon API.
class VpnApi: VpnHostApi {
  func connect(server: String) throws -> Bool {
    return !server.isEmpty
  }

  func disconnect() throws {}
}
