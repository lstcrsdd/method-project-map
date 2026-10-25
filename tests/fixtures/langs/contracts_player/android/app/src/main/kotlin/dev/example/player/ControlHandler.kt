package dev.example.player

import io.flutter.plugin.common.BinaryMessenger
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel

/** The Android side of the `player/control` channel. */
class ControlHandler(messenger: BinaryMessenger) : MethodChannel.MethodCallHandler {
    private val channel = MethodChannel(messenger, "player/control")

    init {
        channel.setMethodCallHandler(this)
    }

    override fun onMethodCall(call: MethodCall, result: MethodChannel.Result) {
        when (call.method) {
            "play" -> result.success(NativeCore.start("current") == 0)
            "pause" -> result.success(null)
            "seek" -> {
                NativeCore.seek(call.argument<Number>("ms")?.toLong() ?: 0L)
                result.success(null)
            }
            "shuffle" -> result.success(null)
            "relase" -> {
                NativeCore.stop()
                result.success(null)
            }
            else -> result.notImplemented()
        }
    }
}
