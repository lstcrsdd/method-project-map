import 'package:flutter/services.dart';

const String kControlChannel = 'app.vpn/control';

/// The platform side of the VPN: macOS (Swift), Android (Kotlin) and Windows (C++).
class PlatformVpn {
  static const MethodChannel _control = MethodChannel(kControlChannel);
  static const EventChannel _state = EventChannel('app.vpn/state');

  void Function()? onRevoked;

  PlatformVpn() {
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
