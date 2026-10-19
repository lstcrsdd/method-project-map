/// One connection to the server.
public final class Session {
    let profile: Profile

    init(profile: Profile) {
        self.profile = profile
    }

    func connect() {}
}
