import Flutter

/// The Apple side: a FlutterPlugin whose `handle` serves one channel, a second channel with its own handler.
public class PlayerPlugin: NSObject, FlutterPlugin {
  private let globalMethods: FlutterMethodChannel

  init(globalMethods: FlutterMethodChannel) {
    self.globalMethods = globalMethods
    super.init()
    self.globalMethods.setMethodCallHandler(handleGlobalMethodCall)
  }

  public static func register(with registrar: FlutterPluginRegistrar) {
    let methods = FlutterMethodChannel(name: "dev.example/player", binaryMessenger: registrar.messenger())
    let globalMethods = FlutterMethodChannel(name: "dev.example/player.global", binaryMessenger: registrar.messenger())
    let instance = PlayerPlugin(globalMethods: globalMethods)
    registrar.addMethodCallDelegate(instance, channel: methods)
  }

  private func handleGlobalMethodCall(call: FlutterMethodCall, result: @escaping FlutterResult) {
    let method = call.method
    if method == "init" {
      result(1)
    } else {
      result(FlutterMethodNotImplemented)
    }
  }

  public func handle(_ call: FlutterMethodCall, result: @escaping FlutterResult) {
    handleAsync(call, result: result)
  }

  private func handleAsync(_ call: FlutterMethodCall, result: @escaping FlutterResult) {
    let method = call.method
    if method == "pause" {
      result(1)
    } else {
      result(FlutterMethodNotImplemented)
    }
  }
}
