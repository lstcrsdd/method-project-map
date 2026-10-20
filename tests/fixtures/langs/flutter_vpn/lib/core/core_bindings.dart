import 'dart:ffi';

import 'package:ffi/ffi.dart';

typedef _VersionNative = Pointer<Utf8> Function();
typedef _Version = Pointer<Utf8> Function();
typedef _ParseNative = Int32 Function(Pointer<Utf8>);
typedef _Parse = int Function(Pointer<Utf8>);

/// Direct calls into the Rust core (libvpn_core) through dart:ffi.
class CoreBindings {
  final DynamicLibrary _lib;

  CoreBindings(this._lib);

  factory CoreBindings.open() => CoreBindings(DynamicLibrary.open('libvpn_core.dylib'));

  String version() {
    final fn = _lib.lookupFunction<_VersionNative, _Version>('vpn_core_version');
    return fn().toDartString();
  }

  int parseConfig(String text) {
    final fn = _lib.lookup<NativeFunction<_ParseNative>>('vpn_core_parse_config').asFunction<_Parse>();
    final ptr = text.toNativeUtf8();
    try {
      return fn(ptr);
    } finally {
      malloc.free(ptr);
    }
  }

  int stats() => vpnCoreStats();
}

@Native<Int64 Function()>(symbol: 'vpn_core_stats')
external int vpnCoreStats();
