// The Android copy: a stream link -> an audio player config (a map the app serialises).
package dev.example.twins

import java.net.URI

object Tag {
    const val REMOTE = "remote"
    const val LOCAL = "local"
}

fun buildConfig(link: String): Map<String, Any?> {
    val uri = URI(link)
    require(uri.scheme == "stream") { "scheme" }
    val host = uri.host?.removePrefix("[")?.removeSuffix("]") ?: throw IllegalArgumentException("host")
    val port = uri.port
    require(host.isNotEmpty() && port in 1..65535) { "host or port" }
    val query = (uri.rawQuery ?: "").split("&").mapNotNull {
        val kv = it.split("=", limit = 2)
        if (kv.size == 2) kv[0] to kv[1] else null
    }.toMap()
    val source = mutableMapOf<String, Any?>(
        "kind" to "stream",
        "tag" to Tag.REMOTE,
        "host" to host,
        "port" to port,
        "user" to (uri.userInfo ?: ""),
        "codec" to mapOf("name" to (query["codec"] ?: "opus")),
        "tls" to mapOf("enabled" to true, "peer_name" to (query["peer"] ?: host)),
    )
    query["eq"]?.let { preset ->
        source["eq"] = mapOf("preset" to preset, "gain" to (query["eq-gain"] ?: "0"))
    }
    val prefix = if (host.contains(":")) "/128" else "/32"
    return mapOf(
        "sources" to listOf(source, mapOf("kind" to "cache", "tag" to Tag.LOCAL)),
        "playback" to mapOf(
            "rules" to listOf(mapOf("peer_cidr" to listOf(host + prefix), "source" to Tag.LOCAL)),
            "fallback" to Tag.REMOTE,
        ),
    )
}

fun describe(link: String): String = "${URI(link).host}"
