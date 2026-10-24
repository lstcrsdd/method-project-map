import 'package:flutter/material.dart';

import 'ui/home_page.dart';
import 'player/player_controller.dart';

void main() {
  final controller = PlayerController();
  runApp(PlayerApp(controller: controller));
}

/// The application: one screen with a connect switch.
class PlayerApp extends StatelessWidget {
  final PlayerController controller;

  const PlayerApp({super.key, required this.controller});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(home: HomePage(controller: controller));
  }
}
