use vpn_core::config::ConfigStore;
use vpn_core::tunnel::{udp::UdpTransport, Tunnel};

fn main() {
    let store = ConfigStore::new("profiles.toml");
    let tunnel = Tunnel::new(store, Box::new(UdpTransport));
    tunnel.connect("home");
    println!("connected");
}
