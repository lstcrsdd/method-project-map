//! The core copy: a stream link -> an audio player config. Reads one link per line on stdin, prints one JSON object
//! (or ERROR) per line. No dependencies: `rustc main.rs` builds it.
use std::collections::BTreeMap;
use std::io::{self, BufRead};

mod tag {
    pub const REMOTE: &str = "remote";
    pub const LOCAL: &str = "local";
}

enum Json {
    Bool(bool),
    Num(u32),
    Str(String),
    List(Vec<Json>),
    Obj(BTreeMap<String, Json>),
}

fn obj(pairs: Vec<(&str, Json)>) -> Json {
    Json::Obj(pairs.into_iter().map(|(k, v)| (k.to_string(), v)).collect())
}

fn text(s: &str) -> Json {
    Json::Str(s.to_string())
}

fn render(v: &Json) -> String {
    match v {
        Json::Bool(b) => b.to_string(),
        Json::Num(n) => n.to_string(),
        Json::Str(s) => format!("\"{}\"", s.replace('\\', "\\\\").replace('"', "\\\"")),
        Json::List(items) => format!("[{}]", items.iter().map(render).collect::<Vec<_>>().join(",")),
        Json::Obj(map) => format!(
            "{{{}}}",
            map.iter().map(|(k, v)| format!("\"{}\":{}", k, render(v))).collect::<Vec<_>>().join(",")
        ),
    }
}

fn query<'a>(q: &'a str, name: &str) -> Option<&'a str> {
    q.split('&').filter_map(|kv| kv.split_once('=')).find(|(k, _)| *k == name).map(|(_, v)| v)
}

fn build_config(link: &str) -> Result<Json, String> {
    let rest = link.strip_prefix("stream://").ok_or("scheme")?;
    let rest = rest.split('#').next().unwrap_or("");
    let (main, q) = rest.split_once('?').unwrap_or((rest, ""));
    let (user, hostport) = main.rsplit_once('@').ok_or("user")?;
    let (host, port) = hostport.rsplit_once(':').ok_or("port")?;
    let host = host.trim_start_matches('[').trim_end_matches(']');
    let port: u32 = port.parse().map_err(|_| "port")?;
    if host.is_empty() || port == 0 || port > 65535 {
        return Err("host or port".into());
    }
    let mut source = vec![
        ("kind", text("stream")),
        ("tag", text(tag::REMOTE)),
        ("host", text(host)),
        ("port", Json::Num(port)),
        ("user", text(user)),
        ("codec", obj(vec![("name", text(query(q, "codec").unwrap_or("opus")))])),
        ("tls", obj(vec![("enabled", Json::Bool(true)), ("peer_name", text(query(q, "peer").unwrap_or(host)))])),
    ];
    if let Some(preset) = query(q, "eq") {
        let gain = query(q, "eq-gain").unwrap_or("0");
        source.push(("eq", obj(vec![("preset", text(preset)), ("gain", text(gain))])));
    }
    let prefix = if host.contains(':') { "/128" } else { "/32" };
    let rule = obj(vec![
        ("peer_cidr", Json::List(vec![Json::Str(format!("{}{}", host, prefix))])),
        ("source", text(tag::LOCAL)),
    ]);
    Ok(obj(vec![
        ("sources", Json::List(vec![obj(source), obj(vec![("kind", text("cache")), ("tag", text(tag::LOCAL))])])),
        ("playback", obj(vec![("rules", Json::List(vec![rule])), ("fallback", text(tag::REMOTE))])),
    ]))
}

fn main() {
    for line in io::stdin().lock().lines() {
        match build_config(line.unwrap_or_default().trim()) {
            Ok(config) => println!("{}", render(&config)),
            Err(_) => println!("ERROR"),
        }
    }
}
