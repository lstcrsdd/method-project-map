// swift-tools-version:5.9
import PackageDescription

let package = Package(name: "VPN", targets: [.target(name: "VPNCore"), .executableTarget(name: "App", dependencies: ["VPNCore"])])
