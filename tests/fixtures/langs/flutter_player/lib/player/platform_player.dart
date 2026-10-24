import 'package:flutter/services.dart';

const String kControlChannel = 'audio/player';

/// The platform side of the player: macOS (Swift), Android (Kotlin) and Windows (C++).
class PlatformPlayer {
  static const MethodChannel _control = MethodChannel(kControlChannel);
  static const EventChannel _state = EventChannel('audio/player/state');

  void Function()? onRevoked;

  PlatformPlayer() {
    _control.setMethodCallHandler(_onNativeCall);
  }

  Future<bool> connect(String server, String coreVersion) async {
    final ok = await _control.invokeMethod<bool>('connect', {'server': server, 'core': coreVersion});
    return ok ?? false;
  }

  Future<void> disconnect() => _control.invokeMethod('disconnect');

  Future<String> status() async => await _control.invokeMethod<String>('status') ?? 'unknown';

  Stream<String> states() => _state.receiveBroadcastStream().map((event) => event as String);

  Future<dynamic> _onNativeCall(MethodCall call) async {
    switch (call.method) {
      case 'onRevoked':
        onRevoked?.call();
        return null;
      default:
        throw MissingPluginException(call.method);
    }
  }
}
