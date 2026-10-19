import VPNCore

let manager = TunnelManager(store: ConfigStore(), transport: UDPTransport())
manager.start(id: "home")
