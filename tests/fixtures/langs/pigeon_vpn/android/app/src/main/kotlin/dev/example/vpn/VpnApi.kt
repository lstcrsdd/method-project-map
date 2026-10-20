package dev.example.vpn

/** The Android implementation of the Pigeon API. */
class VpnApi : VpnHostApi {
    override fun connect(server: String): Boolean = server.isNotEmpty()

    override fun disconnect() {}
}
