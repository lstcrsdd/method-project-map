package com.example.player

import android.content.Intent
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

/** The Android side of the `audio/player` channel. */
class MainActivity : FlutterActivity() {
    private val channelName = "audio/player"

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        val messenger = flutterEngine.dartExecutor.binaryMessenger
        MethodChannel(messenger, channelName).setMethodCallHandler { call, result ->
            when (call.method) {
                "connect" -> {
                    val server = call.argument<String>("server") ?: ""
                    startPlayback(server)
                    result.success(true)
                }
                "disconnect" -> {
                    stopService(Intent(this, PlaybackService::class.java))
                    result.success(null)
                }
                else -> result.notImplemented()
            }
        }
        PlayerStateStream.attach(messenger)
    }

    private fun startPlayback(server: String) {
        val intent = Intent(this, PlaybackService::class.java).putExtra("server", server)
        startService(intent)
    }
}
