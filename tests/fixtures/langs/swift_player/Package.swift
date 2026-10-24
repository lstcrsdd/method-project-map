// swift-tools-version:5.9
import PackageDescription

let package = Package(name: "Player", targets: [.target(name: "PlayerCore"), .executableTarget(name: "App", dependencies: ["PlayerCore"])])
