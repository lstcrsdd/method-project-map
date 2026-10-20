package dev.example.player

import io.flutter.embedding.engine.plugins.FlutterPlugin
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel

/** Two channels, each with its own handler function. */
class PlayerPlugin : FlutterPlugin {
    private lateinit var methods: MethodChannel
    private lateinit var globalMethods: MethodChannel

    override fun onAttachedToEngine(binding: FlutterPlugin.FlutterPluginBinding) {
        methods = MethodChannel(binding.binaryMessenger, "dev.example/player")
        methods.setMethodCallHandler { call, response -> safeCall(call, response, ::methodHandler) }
        globalMethods = MethodChannel(binding.binaryMessenger, "dev.example/player.global")
        globalMethods.setMethodCallHandler { call, response -> safeCall(call, response, ::globalMethodHandler) }
    }

    override fun onDetachedFromEngine(binding: FlutterPlugin.FlutterPluginBinding) {}

    private fun safeCall(call: MethodCall, response: MethodChannel.Result, handler: (MethodCall, MethodChannel.Result) -> Unit) {
        handler(call, response)
    }

    private fun globalMethodHandler(call: MethodCall, response: MethodChannel.Result) {
        when (call.method) {
            "init" -> response.success(1)
            else -> response.notImplemented()
        }
    }

    private fun methodHandler(call: MethodCall, response: MethodChannel.Result) {
        when (call.method) {
            "pause" -> response.success(1)
            "resume" -> response.success(1)
            else -> response.notImplemented()
        }
    }
}
