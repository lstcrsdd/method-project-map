import 'package:flutter/material.dart';
import 'package:player_app/player.dart';

/// The first screen.
class HomePage extends StatelessWidget {
  final PlaybackService service;
  const HomePage({super.key, required this.service});

  @override
  Widget build(BuildContext context) {
    service.connect('home');
    return const StatusCard();
  }
}

class StatusCard extends StatelessWidget {
  const StatusCard({super.key});

  @override
  Widget build(BuildContext context) => Text('connected');
}
