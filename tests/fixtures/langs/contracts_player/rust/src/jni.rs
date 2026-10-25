//! JNI entry points of the audio engine, called from Kotlin (NativeCore.kt).

use jni::objects::{JClass, JString};
use jni::sys::{jboolean, jfloat, jint};
use jni::JNIEnv;

type Rate = f32;

#[no_mangle]
pub extern "system" fn Java_dev_example_player_NativeCore_start<'local>(
    mut env: JNIEnv<'local>,
    _class: JClass<'local>,
    uri: JString<'local>,
) -> jint {
    let uri: String = env.get_string(&uri).map(|s| s.into()).unwrap_or_default();
    uri.len() as jint
}

#[no_mangle]
pub extern "system" fn Java_dev_example_player_NativeCore_stop(_env: JNIEnv, _class: JClass) {}

#[no_mangle]
pub extern "system" fn Java_dev_example_player_NativeCore_setVolume(_env: JNIEnv, _class: JClass, level: jint) {
    let _ = level;
}

#[no_mangle]
pub extern "system" fn Java_dev_example_player_NativeCore_load(_env: JNIEnv, _class: JClass, uri: jboolean) -> jint {
    uri as jint
}

#[no_mangle]
pub extern "system" fn Java_dev_example_player_NativeCore_seek(_env: JNIEnv, _class: JClass, position_ms: i64) {
    let _ = position_ms;
}

#[no_mangle]
pub extern "system" fn Java_dev_example_player_NativeCore_setRate(_env: JNIEnv, _class: JClass, rate: Rate) {
    let _ = rate;
}

#[no_mangle]
pub extern "system" fn Java_dev_example_player_NativeCore_gain(_env: JNIEnv, _class: JClass, db: jfloat) {
    let _ = db;
}
