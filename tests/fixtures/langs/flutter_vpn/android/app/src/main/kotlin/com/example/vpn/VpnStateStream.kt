package com.example.vpn

import io.flutter.plugin.common.BinaryMessenger
import io.flutter.plugin.common.EventChannel

/** Streams tunnel states to Dart over `app.vpn/state`. */
object VpnStateStream {
    fun attach(messenger: BinaryMessenger) {
        EventChannel(messenger, "app.vpn/state").setStreamHandler(object : EventChannel.StreamHandler {
            override fun onListen(arguments: Any?, events: EventChannel.EventSink?) {
                events?.success("disconnected")
            }

            override fun onCancel(arguments: Any?) {}
        })
    }
}
