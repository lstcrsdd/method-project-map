/// A playback profile.
public struct Profile {
    public let id: String
}

/// Loads profiles.
public final class ConfigStore {
    public init() {}

    public func load(_ id: String) -> Profile {
        return Profile(id: id)
    }
}
