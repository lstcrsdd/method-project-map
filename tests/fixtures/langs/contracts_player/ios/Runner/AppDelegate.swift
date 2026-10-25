import Flutter
import UIKit

@main
@objc class AppDelegate: FlutterAppDelegate {
  override func application(
    _ application: UIApplication,
    didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?
  ) -> Bool {
    let controller = window?.rootViewController as! FlutterViewController
    ControlChannel.register(with: controller.binaryMessenger)
    LibraryChannel.register(with: controller.binaryMessenger)
    StatsChannel.register(with: controller.binaryMessenger)
    return super.application(application, didFinishLaunchingWithOptions: launchOptions)
  }
}
