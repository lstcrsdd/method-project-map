package com.example.player

import android.app.Service
import android.content.Intent

/** The Android playback service: keeps the process alive and hands the profile to the Rust core. */
class PlaybackService : Service() {
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val server = intent?.getStringExtra("server") ?: return START_NOT_STICKY
        if (NativeCore.start(server) != 0) {
            stopSelf()
            return START_NOT_STICKY
        }
        // playback itself runs in the Rust core
        return START_STICKY
    }

    override fun onDestroy() {
        NativeCore.stop()
        super.onDestroy()
    }
}
