package dev.example.player

import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine

class MainActivity : FlutterActivity() {
    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        val messenger = flutterEngine.dartExecutor.binaryMessenger
        ControlHandler(messenger)
        LibraryHandler(messenger)
        FocusHandler(messenger)
        EqualizerHandler(messenger)
        Streams.attach(messenger)
    }
}
