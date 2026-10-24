import 'config.dart';

/// Sends packets somewhere.
abstract class Transport {
  void send(List<int> data);
}

class UdpTransport implements Transport {
  @override
  void send(List<int> data) {}
}

/// Plays one stream.
class PlaybackService {
  final ConfigStore store;
  final Transport transport;

  PlaybackService(this.store, this.transport);

  Session connect(String id) {
    final profile = store.load(id);
    final session = Session(profile);
    session.open();
    transport.send([]);
    _log('connected');
    mystery.call();
    return session;
  }

  void _log(String message) {}
}

class Session {
  final Profile profile;
  Session(this.profile);
  void open() {}
}
