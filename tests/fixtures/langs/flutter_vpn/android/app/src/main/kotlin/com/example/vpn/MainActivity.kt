package com.example.vpn

import android.content.Intent
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

/** The Android side of the `app.vpn/control` channel. */
class MainActivity : FlutterActivity() {
    private val channelName = "app.vpn/control"

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        val messenger = flutterEngine.dartExecutor.binaryMessenger
        MethodChannel(messenger, channelName).setMethodCallHandler { call, result ->
            when (call.method) {
                "connect" -> {
                    val server = call.argument<String>("server") ?: ""
                    startTunnel(server)
                    result.success(true)
                }
                "disconnect" -> {
                    stopService(Intent(this, TunnelService::class.java))
                    result.success(null)
                }
                else -> result.notImplemented()
            }
        }
        VpnStateStream.attach(messenger)
    }

    private fun startTunnel(server: String) {
        val intent = Intent(this, TunnelService::class.java).putExtra("server", server)
        startService(intent)
    }
}
