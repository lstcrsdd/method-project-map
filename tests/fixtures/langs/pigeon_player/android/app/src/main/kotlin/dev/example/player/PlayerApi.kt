package dev.example.player

/** The Android implementation of the Pigeon API. */
class PlayerApi : PlayerHostApi {
    override fun connect(server: String): Boolean = server.isNotEmpty()

    override fun disconnect() {}
}
