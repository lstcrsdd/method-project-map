package dev.example.audio_player

import android.content.Context
import android.media.AudioAttributes
import android.media.MediaPlayer
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import io.flutter.embedding.engine.plugins.FlutterPlugin
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel
import io.flutter.plugin.common.MethodChannel.MethodCallHandler
import io.flutter.plugin.common.MethodChannel.Result

/** The Android side of the `audio/player` channel: one MediaPlayer per plugin instance. */
class AudioPlayerPlugin : FlutterPlugin, MethodCallHandler {
    private lateinit var channel: MethodChannel
    private lateinit var context: Context
    private val main = Handler(Looper.getMainLooper())
    private var player: MediaPlayer? = null
    private var volume = 1.0f
    private var speed = 1.0f

    override fun onAttachedToEngine(binding: FlutterPlugin.FlutterPluginBinding) {
        context = binding.applicationContext
        channel = MethodChannel(binding.binaryMessenger, "audio/player")
        channel.setMethodCallHandler(this)
    }

    override fun onDetachedFromEngine(binding: FlutterPlugin.FlutterPluginBinding) {
        channel.setMethodCallHandler(null)
        main.removeCallbacksAndMessages(null)
        release()
    }

    /** Prepares [url] off the main thread and starts it when ready; [result] answers Dart either way. */
    private fun load(url: String, result: Result) {
        release()
        var answered = false
        val mp = MediaPlayer()
        mp.setAudioAttributes(
            AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_MEDIA)
                .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC)
                .build()
        )
        mp.setWakeMode(context, PowerManager.PARTIAL_WAKE_LOCK)  // keeps playing with the screen off
        mp.setOnPreparedListener {
            it.setVolume(volume, volume)
            applySpeed(it)
            it.start()
            answered = true
            result.success(null)
        }
        mp.setOnErrorListener { _, what, extra ->
            if (!answered) {
                answered = true
                result.error("play", "MediaPlayer error $what/$extra", null)
            }
            release()
            true
        }
        mp.setOnCompletionListener { completed() }
        try {
            mp.setDataSource(context, Uri.parse(url))
        } catch (e: Exception) {
            mp.release()
            result.error("bad_url", e.message, null)
            return
        }
        player = mp
        mp.prepareAsync()
    }

    /** Frees the native player; the next `play` creates a new one. */
    private fun release() {
        player?.release()
        player = null
    }

    /** MediaPlayer keeps the speed in its playback parameters, so it is applied again to every new source. */
    private fun applySpeed(mp: MediaPlayer) {
        val wasPlaying = mp.isPlaying
        mp.playbackParams = mp.playbackParams.setSpeed(speed)
        if (!wasPlaying) {
            mp.pause()  // setting a speed starts a paused MediaPlayer
        }
    }

    /** Native to Dart: the source played to its end. MediaPlayer calls this on its own thread. */
    private fun completed() {
        main.post { channel.invokeMethod("onComplete", null) }
    }

    /** Dart to native: one branch per method of `AudioPlayer` (lib/player.dart). */
    override fun onMethodCall(call: MethodCall, result: Result) {
        when (call.method) {
            "play" -> {
                val url = call.argument<String>("url")
                if (url == null) {
                    result.error("bad_url", "play needs a url", null)
                    return
                }
                volume = (call.argument<Double>("volume") ?: 1.0).toFloat()
                load(url, result)
            }
            "resume" -> {
                player?.start()
                result.success(null)
            }
            "pause" -> {
                player?.pause()
                result.success(null)
            }
            "stop" -> {
                release()
                result.success(null)
            }
            "seek" -> {
                player?.seekTo(call.argument<Int>("ms") ?: 0)
                result.success(null)
            }
            "setVolume" -> {
                volume = (call.argument<Double>("volume") ?: 1.0).toFloat()
                player?.setVolume(volume, volume)
                result.success(null)
            }
            "setSpeed" -> {
                speed = (call.argument<Double>("speed") ?: 1.0).toFloat()
                player?.let { applySpeed(it) }
                result.success(null)
            }
            "position" -> result.success(player?.currentPosition ?: 0)
            else -> result.notImplemented()
        }
    }
}
