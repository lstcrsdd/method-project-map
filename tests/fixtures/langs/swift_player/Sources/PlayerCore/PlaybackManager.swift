import Foundation

/// Sends packets somewhere.
public protocol Transport {
    func send(_ data: Data)
}

open class BaseManager {
    func describe() -> String { "base" }
}

/// Starts and stops playback.
public final class PlaybackManager: BaseManager {
    private let store: ConfigStore
    private let transport: Transport

    public init(store: ConfigStore, transport: Transport) {
        self.store = store
        self.transport = transport
    }

    public func start(id: String) -> Session {
        let profile = store.load(id)
        let session = Session(profile: profile)
        session.connect()
        transport.send(Data())
        log("start")
        describe()
        mystery.call()
        return session
    }
}
