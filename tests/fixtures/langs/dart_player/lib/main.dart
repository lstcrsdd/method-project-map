import 'package:flutter/material.dart';
import 'ui/home.dart';
import 'player.dart' as player;

void main() {
  final service = player.PlaybackService(player.ConfigStore(), player.UdpTransport());
  runApp(MaterialApp(home: HomePage(service: service)));
}
