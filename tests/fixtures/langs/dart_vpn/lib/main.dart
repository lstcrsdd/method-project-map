import 'package:flutter/material.dart';
import 'ui/home.dart';
import 'vpn.dart' as vpn;

void main() {
  final service = vpn.TunnelService(vpn.ConfigStore(), vpn.UdpTransport());
  runApp(MaterialApp(home: HomePage(service: service)));
}
