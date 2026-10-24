enum Logger {
    static func write(_ message: String) {
        print(message)
    }
}

extension PlaybackManager {
    func log(_ message: String) {
        Logger.write(message)
    }
}
