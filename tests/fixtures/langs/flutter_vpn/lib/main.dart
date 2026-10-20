import 'package:flutter/material.dart';

import 'ui/home_page.dart';
import 'vpn/vpn_controller.dart';

void main() {
  final controller = VpnController();
  runApp(VpnApp(controller: controller));
}

/// The application: one screen with a connect switch.
class VpnApp extends StatelessWidget {
  final VpnController controller;

  const VpnApp({super.key, required this.controller});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(home: HomePage(controller: controller));
  }
}
