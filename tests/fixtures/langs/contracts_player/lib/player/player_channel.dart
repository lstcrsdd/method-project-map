import 'dart:io' show Platform;

import 'package:flutter/services.dart';

/// The platform side of the player: iOS (Swift) and Android (Kotlin).
class PlayerChannel {
  static const MethodChannel _control = MethodChannel('player/control');
  static const MethodChannel _library = MethodChannel('player/media_library');
  static const MethodChannel _focus = MethodChannel('player/focus');
  static const EventChannel _position = EventChannel('player/position');
  static const EventChannel _levels = EventChannel('player/levels');
  static const EventChannel _visualizer = EventChannel('player/visualizer');

  Future<void> play() => _control.invokeMethod('play');

  Future<void> pause() => _control.invokeMethod('pause');

  Future<void> seek(int ms) => _control.invokeMethod('seek', {'ms': ms});

  Future<void> shuffle(bool on) => _control.invokeMethod('shuffle', {'on': on});

  Future<void> release() => _control.invokeMethod('release');

  Future<List<String>?> tracks() => _library.invokeListMethod<String>('list');

  Future<void> rescan() => _library.invokeMethod('scan');

  Future<bool> requestFocus() async {
    if (Platform.isAndroid) {
      return await _focus.invokeMethod<bool>('request') ?? false;
    }
    return true;
  }

  Stream<int> positions() => _position.receiveBroadcastStream().map((e) => e as int);

  Stream<double> levels() => _levels.receiveBroadcastStream().map((e) => e as double);

  Stream<List<int>> spectrum() => _visualizer.receiveBroadcastStream().map((e) => (e as List).cast<int>());
}
