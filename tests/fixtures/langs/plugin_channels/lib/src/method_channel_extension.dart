import 'package:flutter/services.dart';

extension StandardMethodChannel on MethodChannel {
  Future<void> call(String method, [Map<String, dynamic> args = const {}]) async {
    return invokeMethod<void>(method, args);
  }
}
