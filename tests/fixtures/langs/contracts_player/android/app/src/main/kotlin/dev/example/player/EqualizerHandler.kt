package dev.example.player

import io.flutter.plugin.common.BinaryMessenger
import io.flutter.plugin.common.EventChannel
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel

/** The equalizer: presets and the live band levels (Android only). */
class EqualizerHandler(messenger: BinaryMessenger) : MethodChannel.MethodCallHandler, EventChannel.StreamHandler {
    init {
        MethodChannel(messenger, "eq/settings").setMethodCallHandler(this)
        EventChannel(messenger, "eq/bands").setStreamHandler(this)
    }

    override fun onMethodCall(call: MethodCall, result: MethodChannel.Result) {
        when (call.method) {
            "preset" -> result.success(null)
            else -> result.notImplemented()
        }
    }

    override fun onListen(arguments: Any?, events: EventChannel.EventSink?) {
        events?.success(listOf(0.0, 0.0, 0.0))
    }

    override fun onCancel(arguments: Any?) {}
}
