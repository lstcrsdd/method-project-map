import 'dart:ffi';

import 'package:ffi/ffi.dart';

typedef _OpenNative = Int32 Function(Pointer<Utf8> path, Uint32 flags);
typedef _Open = int Function(Pointer<Utf8> path, int flags);
typedef _SeekNative = Void Function(Int64, Int32);
typedef _Seek = void Function(int, int);
typedef _LogNative = Void Function(Pointer<Utf8>, VarArgs<(Int32,)>);
typedef _Log = void Function(Pointer<Utf8>, int);

/// Direct calls into the audio engine (libplayer_core) through dart:ffi.
class NativeCore {
  final DynamicLibrary _lib;

  NativeCore(this._lib);

  int open(String path, {bool gapless = false}) {
    final fn = _lib.lookupFunction<_OpenNative, _Open>('player_core_open');
    final ptr = path.toNativeUtf8();
    try {
      return fn(ptr, gapless ? 1 : 0);
    } finally {
      malloc.free(ptr);
    }
  }

  void seek(int positionMs) =>
      _lib.lookup<NativeFunction<_SeekNative>>('player_core_seek').asFunction<_Seek>()(positionMs, 0);

  void log(String message, int code) {
    final fn = _lib.lookupFunction<_LogNative, _Log>('player_log');
    final ptr = message.toNativeUtf8();
    fn(ptr, code);
    malloc.free(ptr);
  }

  int position() => playerCorePosition();
}

@Native<Int64 Function()>(symbol: 'player_core_position')
external int playerCorePosition();
