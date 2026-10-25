// The old macOS builder, still used by the menu-bar widget.
import Foundation

func legacyConfig(host: String, port: Int, user: String, peer: String?) -> [String: Any] {
    let source: [String: Any] = [
        "kind": "stream",
        "tag": Tag.remote,
        "host": host,
        "port": port,
        "user": user,
        "codec": ["name": "opus"],
        "tls": ["enabled": true, "peer_name": peer ?? host],
    ]
    return [
        "sources": [source, ["kind": "cache", "tag": Tag.local]],
        "playback": ["rules": [["peer_cidr": [host + "/32"], "source": Tag.local]], "fallback": Tag.remote],
    ]
}

func statusLine(_ config: [String: Any]) -> String {
    let playback = config["playback"] as? [String: Any]
    return "fallback: \(playback?["fallback"] ?? "none")"
}
