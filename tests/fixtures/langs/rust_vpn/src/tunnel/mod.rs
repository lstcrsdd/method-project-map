mod session;
pub mod udp;

pub use session::Session;
use crate::config::{ConfigStore, Profile};

/// Sends packets somewhere.
pub trait Transport {
    fn send(&self, data: &[u8]);
}

/// Keeps one VPN connection.
pub struct Tunnel {
    store: ConfigStore,
    transport: Box<dyn Transport>,
}

impl Tunnel {
    pub fn new(store: ConfigStore, transport: Box<dyn Transport>) -> Self {
        Tunnel { store, transport }
    }

    pub fn connect(&self, name: &str) -> Session {
        let profile: Profile = self.store.load(name);
        let session = Session::open(&profile);
        session.handshake();
        self.transport.send(&[]);
        self.log("connected");
        mystery::call();
        session
    }

    fn log(&self, message: &str) {}
}
