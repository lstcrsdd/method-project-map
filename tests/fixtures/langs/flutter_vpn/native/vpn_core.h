// Generated with cbindgen from rust/src/ffi.rs: the C ABI of the Rust core.
#pragma once

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

int32_t vpn_core_start(const char *config);

void vpn_core_stop(void);

const char *vpn_core_version(void);

int32_t vpn_core_parse_config(const char *text);

int64_t vpn_core_stats(void);

#ifdef __cplusplus
}  // extern "C"
#endif
