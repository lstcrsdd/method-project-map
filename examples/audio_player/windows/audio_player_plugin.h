#ifndef FLUTTER_PLUGIN_AUDIO_PLAYER_PLUGIN_H_
#define FLUTTER_PLUGIN_AUDIO_PLAYER_PLUGIN_H_

#include <flutter/method_channel.h>
#include <flutter/plugin_registrar_windows.h>

#include <memory>

namespace audio_player {

// The Windows side of the `audio/player` channel: plays one source through MCI.
class AudioPlayerPlugin : public flutter::Plugin {
 public:
  static void RegisterWithRegistrar(flutter::PluginRegistrarWindows* registrar);

  AudioPlayerPlugin();
  virtual ~AudioPlayerPlugin();

  AudioPlayerPlugin(const AudioPlayerPlugin&) = delete;
  AudioPlayerPlugin& operator=(const AudioPlayerPlugin&) = delete;

  void HandleMethodCall(const flutter::MethodCall<flutter::EncodableValue>& method_call,
                        std::unique_ptr<flutter::MethodResult<flutter::EncodableValue>> result);
};

}  // namespace audio_player

#endif  // FLUTTER_PLUGIN_AUDIO_PLAYER_PLUGIN_H_
