// Small JNI helpers written in C++ (libplayer_extras.so).
#include <jni.h>

extern "C" JNIEXPORT jint JNICALL
Java_dev_example_player_NativeCore_version(JNIEnv* env, jobject thiz) {
  return 3;
}
