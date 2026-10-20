//! Functions Dart calls through flutter_rust_bridge.

#[flutter_rust_bridge::frb(sync)]
pub fn validate_config(text: String) -> bool {
    crate::config::parse(&text).is_ok()
}
