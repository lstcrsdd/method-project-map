import 'package:flutter/material.dart';

import '../vpn/vpn_controller.dart';

/// Connect switch, server field and the current state.
class HomePage extends StatefulWidget {
  final VpnController controller;

  const HomePage({super.key, required this.controller});

  @override
  State<HomePage> createState() => _HomePageState();
}

class _HomePageState extends State<HomePage> {
  final _server = TextEditingController(text: 'nl-1.example.net');

  Future<void> _toggle(bool on) async {
    if (on) {
      await widget.controller.connect(_server.text);
    } else {
      await widget.controller.disconnect();
    }
    setState(() {});
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: Column(children: [
        TextField(controller: _server),
        Switch(value: widget.controller.connected, onChanged: _toggle),
        Text(widget.controller.stateLabel),
      ]),
    );
  }
}
