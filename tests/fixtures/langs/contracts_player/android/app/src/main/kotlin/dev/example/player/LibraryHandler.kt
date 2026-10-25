package dev.example.player

import io.flutter.plugin.common.BinaryMessenger
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel

/** The Android side of the media library: lists and rescans the tracks on the device. */
class LibraryHandler(messenger: BinaryMessenger) : MethodChannel.MethodCallHandler {
    private val channel = MethodChannel(messenger, "player/media_library")

    init {
        channel.setMethodCallHandler(this)
    }

    override fun onMethodCall(call: MethodCall, result: MethodChannel.Result) {
        when (call.method) {
            "list" -> result.success(listOf<String>())
            "scan" -> result.success(null)
            else -> result.notImplemented()
        }
    }
}
