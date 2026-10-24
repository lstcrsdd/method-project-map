package com.example.player

import io.flutter.plugin.common.BinaryMessenger
import io.flutter.plugin.common.EventChannel

/** Streams player states to Dart over `audio/player/state`. */
object PlayerStateStream {
    fun attach(messenger: BinaryMessenger) {
        EventChannel(messenger, "audio/player/state").setStreamHandler(object : EventChannel.StreamHandler {
            override fun onListen(arguments: Any?, events: EventChannel.EventSink?) {
                events?.success("disconnected")
            }

            override fun onCancel(arguments: Any?) {}
        })
    }
}
