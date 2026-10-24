# audio_player

An example for the links between languages: a Flutter plugin that plays audio through each platform's own player. Dart (`lib/player.dart`) calls the `audio/player` channel; Swift (`ios/Classes`), Kotlin (`android/`) and C++ (`windows/`) handle it, and Swift and Kotlin call back into Dart when a source ends. Only the code is here, without build files (podspec, Gradle, CMake, the Windows registration API).

The Windows side has no branch for `setSpeed`, so the map reports a gap:

```bash
cd examples/audio_player
pmap check .
pmap show sym:lib/player.dart:AudioPlayer.setSpeed
```

The main [README](../../README.md#links-between-languages) shows the output, and the note an agent gets after changing `AudioPlayer.pause`.
