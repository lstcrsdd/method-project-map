use super::Transport;

/// Packets over UDP.
pub struct UdpTransport;

impl Transport for UdpTransport {
    fn send(&self, data: &[u8]) {}
}
