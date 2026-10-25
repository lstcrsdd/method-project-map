package dev.example.player

/** JNI entry points of the audio engine (libplayer_core.so and libplayer_extras.so). */
object NativeCore {
    init {
        System.loadLibrary("player_core")
        System.loadLibrary("player_extras")
    }

    external fun start(uri: String): Int

    external fun stop()

    external fun setVolume(level: Int, fadeMs: Int)

    external fun load(uri: String): Int

    external fun seek(positionMs: Long)

    external fun setRate(rate: Float)

    external fun gain(db: Float)

    external fun gain(db: Float, rampMs: Int)

    external fun version(): Int
}
