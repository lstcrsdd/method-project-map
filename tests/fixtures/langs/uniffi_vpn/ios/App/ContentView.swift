import SwiftUI

/// The iOS screen: connects through the Rust core.
struct ContentView: View {
    @State private var label = "disconnected"

    var body: some View {
        Button("Connect") {
            if connect(server: "nl-1.example.net") {
                label = VpnClient(server: "nl-1.example.net").status()
            }
        }
        Text(label)
    }
}
