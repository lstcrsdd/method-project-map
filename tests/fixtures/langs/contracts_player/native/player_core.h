// The C ABI of the audio engine (rust/src/ffi.rs), for Swift and C++.
#pragma once

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

int32_t player_core_open(const char *path);

void player_core_close(void);

void player_core_seek(int64_t position_ms, int32_t whence);

int64_t player_core_position(void);

void player_core_on_event(void *context, void (*callback)(void *, int32_t));

void player_log(const char *format, ...);

#ifdef __cplusplus
}  // extern "C"
#endif
