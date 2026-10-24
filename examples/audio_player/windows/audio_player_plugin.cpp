#include "audio_player_plugin.h"

#include <windows.h>
#include <mmsystem.h>

#include <flutter/method_channel.h>
#include <flutter/plugin_registrar_windows.h>
#include <flutter/standard_method_codec.h>

#include <memory>
#include <string>

namespace audio_player {
namespace {

// Sends one MCI command; true when MCI accepted it. The plugin plays one source, opened as "track".
bool Mci(const std::wstring& command, std::wstring* reply = nullptr) {
  wchar_t buffer[128] = {};
  MCIERROR error = mciSendStringW(command.c_str(), buffer, 128, nullptr);
  if (reply != nullptr) *reply = buffer;
  return error == 0;
}

std::wstring Widen(const std::string& text) {
  int size = MultiByteToWideChar(CP_UTF8, 0, text.c_str(), -1, nullptr, 0);
  std::wstring out(size > 0 ? size - 1 : 0, L'\0');
  MultiByteToWideChar(CP_UTF8, 0, text.c_str(), -1, out.data(), size);
  return out;
}

const flutter::EncodableValue* Arg(const flutter::MethodCall<flutter::EncodableValue>& call, const char* key) {
  const auto* args = std::get_if<flutter::EncodableMap>(call.arguments());
  if (args == nullptr) return nullptr;
  auto it = args->find(flutter::EncodableValue(key));
  return it == args->end() ? nullptr : &it->second;
}

}  // namespace

// `setSpeed` has no branch here yet: on Windows it ends in NotImplemented (MissingPluginException in Dart).
void AudioPlayerPlugin::HandleMethodCall(const flutter::MethodCall<flutter::EncodableValue>& method_call,
                                         std::unique_ptr<flutter::MethodResult<flutter::EncodableValue>> result) {
  if (method_call.method_name().compare("play") == 0) {
    const auto* url = Arg(method_call, "url");
    Mci(L"close track");
    if (url == nullptr || !std::holds_alternative<std::string>(*url)) {
      result->Error("bad_url", "play needs a url");
    } else if (Mci(L"open \"" + Widen(std::get<std::string>(*url)) + L"\" type mpegvideo alias track") &&
               Mci(L"play track")) {
      result->Success();
    } else {
      result->Error("play", "MCI could not open the source");
    }
  } else if (method_call.method_name().compare("resume") == 0) {
    Mci(L"resume track");
    result->Success();
  } else if (method_call.method_name().compare("pause") == 0) {
    Mci(L"pause track");
    result->Success();
  } else if (method_call.method_name().compare("stop") == 0) {
    Mci(L"close track");
    result->Success();
  } else if (method_call.method_name().compare("seek") == 0) {
    const auto* ms = Arg(method_call, "ms");
    int position = ms != nullptr && std::holds_alternative<int32_t>(*ms) ? std::get<int32_t>(*ms) : 0;
    std::wstring mode;
    Mci(L"status track mode", &mode);
    Mci(L"set track time format milliseconds");
    Mci(L"seek track to " + std::to_wstring(position));
    if (mode == L"playing") Mci(L"play track");  // MCI stops at a seek
    result->Success();
  } else if (method_call.method_name().compare("setVolume") == 0) {
    const auto* volume = Arg(method_call, "volume");
    double value = volume != nullptr && std::holds_alternative<double>(*volume) ? std::get<double>(*volume) : 1.0;
    Mci(L"setaudio track volume to " + std::to_wstring(static_cast<int>(value * 1000)));
    result->Success();
  } else if (method_call.method_name().compare("position") == 0) {
    std::wstring reply;
    Mci(L"set track time format milliseconds");
    Mci(L"status track position", &reply);
    result->Success(flutter::EncodableValue(reply.empty() ? 0 : std::stoi(reply)));
  } else {
    result->NotImplemented();
  }
}

// static
void AudioPlayerPlugin::RegisterWithRegistrar(flutter::PluginRegistrarWindows* registrar) {
  auto channel = std::make_unique<flutter::MethodChannel<flutter::EncodableValue>>(
      registrar->messenger(), "audio/player", &flutter::StandardMethodCodec::GetInstance());
  auto plugin = std::make_unique<AudioPlayerPlugin>();
  channel->SetMethodCallHandler([plugin_pointer = plugin.get()](const auto& call, auto result) {
    plugin_pointer->HandleMethodCall(call, std::move(result));
  });
  registrar->AddPlugin(std::move(plugin));
}

AudioPlayerPlugin::AudioPlayerPlugin() {}

AudioPlayerPlugin::~AudioPlayerPlugin() { Mci(L"close track"); }

}  // namespace audio_player
