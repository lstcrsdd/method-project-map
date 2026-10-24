use player_core::config::ConfigStore;
use player_core::player::{udp::UdpTransport, Player};

fn main() {
    let store = ConfigStore::new("profiles.toml");
    let player = Player::new(store, Box::new(UdpTransport));
    player.connect("home");
    println!("connected");
}
