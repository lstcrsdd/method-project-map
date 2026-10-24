import 'src/messages.g.dart';

/// Connects through the Pigeon-generated API.
Future<void> connectTo(String server) async {
  final api = PlayerHostApi();
  await api.connect(server);
}

Future<void> stop() => PlayerHostApi().disconnect();
