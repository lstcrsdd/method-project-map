package dev.example.player

import io.flutter.plugin.common.BinaryMessenger
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel

/** Audio focus exists on Android only: Dart calls `player/focus` there and nowhere else. */
class FocusHandler(messenger: BinaryMessenger) : MethodChannel.MethodCallHandler {
    private val channel = MethodChannel(messenger, "player/focus")

    init {
        channel.setMethodCallHandler(this)
    }

    override fun onMethodCall(call: MethodCall, result: MethodChannel.Result) {
        if (call.method == "request") {
            result.success(true)
        } else {
            result.notImplemented()
        }
    }
}
