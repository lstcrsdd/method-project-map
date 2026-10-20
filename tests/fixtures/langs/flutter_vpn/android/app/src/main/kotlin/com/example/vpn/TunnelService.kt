package com.example.vpn

import android.content.Intent
import android.net.VpnService

/** The Android VPN service: builds the interface and hands the profile to the Rust core. */
class TunnelService : VpnService() {
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val server = intent?.getStringExtra("server") ?: return START_NOT_STICKY
        if (NativeCore.start(server) != 0) {
            stopSelf()
            return START_NOT_STICKY
        }
        Builder().addAddress("10.0.0.2", 32).establish()
        return START_STICKY
    }

    override fun onDestroy() {
        NativeCore.stop()
        super.onDestroy()
    }
}
