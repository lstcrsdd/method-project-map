import 'package:flutter/material.dart';

import '../player.dart';

/// Play, pause and stop buttons and a speed menu for one [AudioPlayer].
class Controls extends StatelessWidget {
  final AudioPlayer player;
  final String url;

  const Controls({super.key, required this.player, required this.url});

  void onPlay() {
    if (player.state == PlayerState.paused) {
      player.resume();
    } else {
      player.play(url);
    }
  }

  /// Pauses only what is playing: a source that is still loading has nothing to pause yet.
  void onPause() {
    if (player.state != PlayerState.playing) return;
    player.pause();
  }

  @override
  Widget build(BuildContext context) {
    return StreamBuilder<PlayerState>(
      stream: player.states,
      builder: (context, snapshot) => Row(children: [
        IconButton(icon: const Icon(Icons.play_arrow), onPressed: onPlay),
        IconButton(icon: const Icon(Icons.pause), onPressed: onPause),
        IconButton(icon: const Icon(Icons.stop), onPressed: player.stop),
        PopupMenuButton<double>(
          icon: const Icon(Icons.speed),
          onSelected: player.setSpeed,
          itemBuilder: (context) => [
            for (final speed in const [0.5, 1.0, 1.5, 2.0]) PopupMenuItem(value: speed, child: Text('$speed×')),
          ],
        ),
      ]),
    );
  }
}
