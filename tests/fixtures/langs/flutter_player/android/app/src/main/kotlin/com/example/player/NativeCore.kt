package com.example.player

/** JNI entry points of the Rust core (libplayer_core.so). */
object NativeCore {
    init {
        System.loadLibrary("player_core")
    }

    external fun start(config: String): Int

    external fun stop()
}
