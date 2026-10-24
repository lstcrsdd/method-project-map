//! The playback engine itself (a stub in this test project).

use std::sync::atomic::{AtomicI64, Ordering};

use crate::config::Config;

static SENT: AtomicI64 = AtomicI64::new(0);

pub fn start(config: &Config) -> i32 {
    if config.server.is_empty() {
        return -1;
    }
    SENT.store(0, Ordering::SeqCst);
    0
}

pub fn stop() {
    SENT.store(0, Ordering::SeqCst);
}

pub fn bytes_sent() -> i64 {
    SENT.load(Ordering::SeqCst)
}
