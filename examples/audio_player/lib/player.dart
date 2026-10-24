import 'dart:async';

import 'package:flutter/services.dart';

/// What the platform player is doing.
enum PlayerState { stopped, playing, paused, completed }

/// Plays one audio source at a time through the platform's own player
/// (AVPlayer on iOS, MediaPlayer on Android, MCI on Windows). Each call goes
/// over the `audio/player` channel; the platform calls back with `onComplete`
/// when a source plays to its end.
class AudioPlayer {
  static const MethodChannel _channel = MethodChannel('audio/player');

  final StreamController<PlayerState> _states = StreamController<PlayerState>.broadcast();
  PlayerState _state = PlayerState.stopped;

  AudioPlayer() {
    _channel.setMethodCallHandler(_onNativeCall);
  }

  /// The current state.
  PlayerState get state => _state;

  /// Every change of [state], for the UI.
  Stream<PlayerState> get states => _states.stream;

  /// Loads [url] and plays it from the start.
  Future<void> play(String url, {double volume = 1.0}) async {
    await _channel.invokeMethod('play', {'url': url, 'volume': volume});
    _setState(PlayerState.playing);
  }

  /// Continues from where [pause] stopped.
  Future<void> resume() async {
    await _channel.invokeMethod('resume');
    _setState(PlayerState.playing);
  }

  /// Pauses playback; [resume] continues from the same position.
  Future<void> pause() async {
    await _channel.invokeMethod('pause');
    _setState(PlayerState.paused);
  }

  /// Stops playback and unloads the source.
  Future<void> stop() async {
    await _channel.invokeMethod('stop');
    _setState(PlayerState.stopped);
  }

  /// Moves to [position] in the current source.
  Future<void> seek(Duration position) => _channel.invokeMethod('seek', {'ms': position.inMilliseconds});

  /// From 0.0 (silent) to 1.0 (full).
  Future<void> setVolume(double volume) => _channel.invokeMethod('setVolume', {'volume': volume});

  /// 1.0 is normal speed, 2.0 twice as fast.
  Future<void> setSpeed(double speed) => _channel.invokeMethod('setSpeed', {'speed': speed});

  /// How far the current source has played.
  Future<Duration> position() async {
    final ms = await _channel.invokeMethod<int>('position') ?? 0;
    return Duration(milliseconds: ms);
  }

  Future<void> dispose() async {
    await stop();
    _channel.setMethodCallHandler(null);
    await _states.close();
  }

  Future<dynamic> _onNativeCall(MethodCall call) async {
    switch (call.method) {
      case 'onComplete':
        _setState(PlayerState.completed);
        return null;
      default:
        throw MissingPluginException(call.method);
    }
  }

  void _setState(PlayerState state) {
    _state = state;
    _states.add(state);
  }
}
