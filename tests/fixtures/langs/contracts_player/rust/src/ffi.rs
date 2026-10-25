//! The C ABI of the audio engine: called from Swift and C++ (through player_core.h) and from Dart (dart:ffi).

use std::ffi::{c_char, c_void, CStr};

/// Opens a track; `flags` selects gapless playback.
#[no_mangle]
pub extern "C" fn player_core_open(path: *const c_char, flags: u32) -> i32 {
    let path = unsafe { CStr::from_ptr(path) }.to_string_lossy();
    if path.is_empty() || flags > 1 {
        -1
    } else {
        0
    }
}

#[no_mangle]
pub extern "C" fn player_core_close() {}

#[no_mangle]
pub extern "C" fn player_core_seek(position_ms: i64, whence: i32) {
    let _ = (position_ms, whence);
}

#[no_mangle]
pub extern "C" fn player_core_position() -> i64 {
    0
}

#[no_mangle]
pub extern "C" fn player_core_on_event(
    context: *mut c_void,
    callback: extern "C" fn(*mut c_void, i32),
) {
    callback(context, 0);
}
