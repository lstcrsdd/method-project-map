//! The C ABI of the core: called from Swift and C++ (through vpn_core.h), Dart (dart:ffi) and Kotlin (JNI).

use std::ffi::{c_char, CStr};

use jni::objects::{JClass, JString};
use jni::sys::jint;
use jni::JNIEnv;

use crate::config;
use crate::tunnel;

static VERSION: &[u8] = b"0.3.1\0";

#[no_mangle]
pub extern "C" fn vpn_core_start(profile: *const c_char) -> i32 {
    let text = unsafe { CStr::from_ptr(profile) }.to_string_lossy();
    match config::parse(&text) {
        Ok(cfg) => tunnel::start(&cfg),
        Err(_) => -1,
    }
}

#[no_mangle]
pub extern "C" fn vpn_core_stop() {
    tunnel::stop();
}

#[no_mangle]
pub extern "C" fn vpn_core_version() -> *const c_char {
    VERSION.as_ptr() as *const c_char
}

#[no_mangle]
pub extern "C" fn vpn_core_parse_config(text: *const c_char) -> i32 {
    let text = unsafe { CStr::from_ptr(text) }.to_string_lossy();
    if config::parse(&text).is_ok() {
        0
    } else {
        -1
    }
}

#[unsafe(no_mangle)]
pub extern "C" fn vpn_core_stats() -> i64 {
    tunnel::bytes_sent()
}

#[no_mangle]
pub extern "system" fn Java_com_example_vpn_NativeCore_start(mut env: JNIEnv, _class: JClass, profile: JString) -> jint {
    let text: String = env.get_string(&profile).map(|s| s.into()).unwrap_or_default();
    match config::parse(&text) {
        Ok(cfg) => tunnel::start(&cfg),
        Err(_) => -1,
    }
}

#[no_mangle]
pub extern "system" fn Java_com_example_vpn_NativeCore_stop(_env: JNIEnv, _class: JClass) {
    tunnel::stop();
}
