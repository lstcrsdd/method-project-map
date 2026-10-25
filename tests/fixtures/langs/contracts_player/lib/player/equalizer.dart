import 'package:flutter/services.dart';

/// The equalizer exists on Android only: iOS has no channel of this namespace at all.
class Equalizer {
  static const MethodChannel _settings = MethodChannel('eq/settings');
  static const EventChannel _bands = EventChannel('eq/bands');

  Future<void> setPreset(String name) => _settings.invokeMethod('preset', {'name': name});

  Stream<List<double>> bands() => _bands.receiveBroadcastStream().map((e) => (e as List).cast<double>());
}
