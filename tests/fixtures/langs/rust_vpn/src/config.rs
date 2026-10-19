/// A VPN profile.
pub struct Profile {
    pub name: String,
    pub server: String,
}

/// Loads profiles from disk.
pub struct ConfigStore {
    path: String,
}

impl ConfigStore {
    pub fn new(path: &str) -> Self {
        ConfigStore { path: path.to_string() }
    }

    pub fn load(&self, name: &str) -> Profile {
        Profile { name: name.to_string(), server: crate::util::default_server() }
    }
}
