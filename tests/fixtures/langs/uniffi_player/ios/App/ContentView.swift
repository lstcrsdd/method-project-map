import SwiftUI

/// The iOS screen: connects through the Rust core.
struct ContentView: View {
    @State private var label = "disconnected"

    var body: some View {
        Button("Connect") {
            if connect(server: "stream.example.net") {
                label = PlayerClient(server: "stream.example.net").status()
            }
        }
        Text(label)
    }
}
