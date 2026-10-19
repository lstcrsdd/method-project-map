enum Logger {
    static func write(_ message: String) {
        print(message)
    }
}

extension TunnelManager {
    func log(_ message: String) {
        Logger.write(message)
    }
}
