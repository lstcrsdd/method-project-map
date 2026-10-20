import 'package:pigeon/pigeon.dart';

@HostApi()
abstract class VpnHostApi {
  bool connect(String server);
  void disconnect();
}
