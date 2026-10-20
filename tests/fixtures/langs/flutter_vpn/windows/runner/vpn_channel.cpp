#include "vpn_channel.h"

#include "../../native/vpn_core.h"

#include <string>

VpnChannel::VpnChannel(flutter::BinaryMessenger* messenger) {
  channel_ = std::make_unique<flutter::MethodChannel<flutter::EncodableValue>>(
      messenger, "app.vpn/control", &flutter::StandardMethodCodec::GetInstance());
  channel_->SetMethodCallHandler([this](const auto& call, auto result) { HandleMethodCall(call, std::move(result)); });
}

void VpnChannel::HandleMethodCall(const flutter::MethodCall<flutter::EncodableValue>& call,
                                  std::unique_ptr<flutter::MethodResult<flutter::EncodableValue>> result) {
  if (call.method_name() == "connect") {
    const auto* args = std::get_if<flutter::EncodableMap>(call.arguments());
    std::string server = std::get<std::string>(args->at(flutter::EncodableValue("server")));
    result->Success(flutter::EncodableValue(vpn_core_start(server.c_str()) == 0));
  } else if (call.method_name().compare("disconnect") == 0) {
    vpn_core_stop();
    result->Success();
  } else if (call.method_name() == "status") {
    result->Success(flutter::EncodableValue("disconnected"));
  } else {
    result->NotImplemented();
  }
}
