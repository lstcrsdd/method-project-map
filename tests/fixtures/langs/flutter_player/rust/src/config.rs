//! Stream profiles: `server = host` lines.

#[derive(Debug, Clone)]
pub struct Config {
    pub server: String,
}

#[derive(Debug)]
pub struct ParseError(pub String);

pub fn parse(text: &str) -> Result<Config, ParseError> {
    for line in text.lines() {
        if let Some(rest) = line.trim().strip_prefix("server") {
            let server = rest.trim_start_matches([' ', '=']).trim();
            if !server.is_empty() {
                return Ok(Config { server: server.to_string() });
            }
        }
    }
    Err(ParseError("no server".into()))
}
