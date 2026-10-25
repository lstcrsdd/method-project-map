package dev.example.player

import io.flutter.plugin.common.BinaryMessenger
import io.flutter.plugin.common.EventChannel

/** Streams playback statistics to Dart: position, levels and the visualizer spectrum. */
object Streams {
    fun attach(messenger: BinaryMessenger) {
        EventChannel(messenger, "player/position").setStreamHandler(PositionHandler())
        EventChannel(messenger, "player/levels").setStreamHandler(LevelHandler())
        EventChannel(messenger, "player/visualizer").setStreamHandler(SpectrumHandler())
    }
}

class PositionHandler : EventChannel.StreamHandler {
    override fun onListen(arguments: Any?, events: EventChannel.EventSink?) {
        events?.success(0)
    }

    override fun onCancel(arguments: Any?) {}
}

class LevelHandler : EventChannel.StreamHandler {
    override fun onListen(arguments: Any?, events: EventChannel.EventSink?) {
        events?.success(0.0)
    }

    override fun onCancel(arguments: Any?) {}
}

class SpectrumHandler : EventChannel.StreamHandler {
    override fun onListen(arguments: Any?, events: EventChannel.EventSink?) {
        events?.success(listOf(0, 0, 0))
    }

    override fun onCancel(arguments: Any?) {}
}
