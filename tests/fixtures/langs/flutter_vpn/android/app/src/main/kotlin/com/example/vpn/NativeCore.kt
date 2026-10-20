package com.example.vpn

/** JNI entry points of the Rust core (libvpn_core.so). */
object NativeCore {
    init {
        System.loadLibrary("vpn_core")
    }

    external fun start(config: String): Int

    external fun stop()
}
