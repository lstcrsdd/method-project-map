import 'src/messages.g.dart';

/// Connects through the Pigeon-generated API.
Future<void> connectTo(String server) async {
  final api = VpnHostApi();
  await api.connect(server);
}

Future<void> stop() => VpnHostApi().disconnect();
