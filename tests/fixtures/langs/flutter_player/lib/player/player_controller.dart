import '../core/core_bindings.dart';
import '../src/rust/api/config.dart';
import 'platform_player.dart';

/// What the UI talks to: checks the profile in Rust, then asks the platform to connect.
class PlayerController {
  final PlatformPlayer _platform = PlatformPlayer();
  final CoreBindings _core = CoreBindings.open();
  bool connected = false;
  String stateLabel = 'disconnected';

  PlayerController() {
    _platform.states().listen((state) => stateLabel = state);
    _platform.onRevoked = () => connected = false;
  }

  Future<void> connect(String server) async {
    final profile = 'server = $server';
    if (!validateConfig(text: profile)) {
      throw ArgumentError('bad profile for $server');
    }
    connected = await _platform.connect(server, _core.version());
  }

  Future<void> disconnect() async {
    await _platform.disconnect();
    connected = false;
  }

  Future<String> status() => _platform.status();

  int trafficBytes() => _core.stats();
}
