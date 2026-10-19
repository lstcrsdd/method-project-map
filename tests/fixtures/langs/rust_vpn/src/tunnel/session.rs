use crate::config::Profile;

/// One handshake with the server.
pub struct Session {
    server: String,
}

impl Session {
    pub fn open(profile: &Profile) -> Self {
        Session { server: profile.server.clone() }
    }

    pub fn handshake(&self) -> bool {
        true
    }
}
