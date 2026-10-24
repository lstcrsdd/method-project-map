import PlayerCore

let manager = PlaybackManager(store: ConfigStore(), transport: UDPTransport())
manager.start(id: "home")
