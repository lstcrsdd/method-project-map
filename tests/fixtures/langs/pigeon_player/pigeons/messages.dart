import 'package:pigeon/pigeon.dart';

@HostApi()
abstract class PlayerHostApi {
  bool connect(String server);
  void disconnect();
}
