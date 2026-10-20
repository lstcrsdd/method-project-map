import 'package:flutter/services.dart';

import 'method_channel_extension.dart';

/// Talks to the native player through two channels.
class PlayerPlatform {
  static const MethodChannel _methods = MethodChannel('dev.example/player');
  static const MethodChannel _global = MethodChannel('dev.example/player.global');

  Future<void> init() => _global.call('init');

  Future<void> pause(String playerId) => _send('pause', playerId);

  Future<void> resume(String playerId) => _send('resume', playerId);

  Future<void> _send(String method, String playerId, [Map<String, dynamic> extra = const {}]) {
    return _methods.call(method, {'playerId': playerId, ...extra});
  }
}
