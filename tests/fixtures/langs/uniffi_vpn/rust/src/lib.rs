//! The VPN core exported to Swift and Kotlin with UniFFI.

uniffi::setup_scaffolding!();

#[uniffi::export]
pub fn connect(server: String) -> bool {
    !server.is_empty()
}

#[derive(uniffi::Object)]
pub struct VpnClient {
    server: String,
}

#[uniffi::export]
impl VpnClient {
    #[uniffi::constructor]
    pub fn new(server: String) -> Self {
        Self { server }
    }

    pub fn status(&self) -> String {
        format!("connected to {}", self.server)
    }
}
