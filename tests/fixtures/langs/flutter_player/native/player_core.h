// Generated with cbindgen from rust/src/ffi.rs: the C ABI of the Rust core.
#pragma once

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

int32_t player_core_start(const char *config);

void player_core_stop(void);

const char *player_core_version(void);

int32_t player_core_parse_config(const char *text);

int64_t player_core_stats(void);

#ifdef __cplusplus
}  // extern "C"
#endif
