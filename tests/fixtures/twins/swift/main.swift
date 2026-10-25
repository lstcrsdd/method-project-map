// The macOS copy: a stream link -> an audio player config. Reads one link per line on stdin, prints one JSON object
// (or ERROR) per line.
import Foundation

enum Tag {
    static let remote = "remote"
    static let local = "local"
}

enum LinkError: Error {
    case invalid
}

func buildConfig(_ link: String) throws -> [String: Any] {
    guard let url = URLComponents(string: link), url.scheme == "stream" else {
        throw LinkError.invalid
    }
    guard let host = url.host, !host.isEmpty, let port = url.port, port > 0 else {
        throw LinkError.invalid
    }
    let items = url.queryItems ?? []
    func query(_ name: String) -> String? { items.first(where: { $0.name == name })?.value }
    // URLComponents keeps the brackets of an IPv6 host.
    var source: [String: Any] = [
        "kind": "stream",
        "tag": Tag.remote,
        "host": host,
        "port": port,
        "user": url.user ?? "",
        "codec": ["name": query("codec") ?? "opus"],
        "tls": ["enabled": true, "peer_name": query("peer") ?? host],
    ]
    if let preset = query("eq") {
        source["eq"] = ["preset": preset, "gain": query("eq-gain") ?? "0"]
    }
    let prefix = host.contains(":") ? "/128" : "/32"
    return [
        "sources": [source, ["kind": "cache", "tag": Tag.local]],
        "playback": ["rules": [["peer_cidr": [host + prefix], "source": Tag.local]], "fallback": Tag.remote],
    ]
}

while let line = readLine() {
    if let config = try? buildConfig(line),
       let data = try? JSONSerialization.data(withJSONObject: config, options: [.sortedKeys, .withoutEscapingSlashes]) {
        print(String(data: data, encoding: .utf8) ?? "ERROR")
    } else {
        print("ERROR")
    }
}
