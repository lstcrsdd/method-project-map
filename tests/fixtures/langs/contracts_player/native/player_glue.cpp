// Desktop glue: drives the engine from C++.
#include "player_core.h"

#define SEEK_TO_START 0, 0

void glue_rewind() {
  player_core_seek(SEEK_TO_START);
}

void glue_jump(int64_t ms) {
  player_core_seek(ms, 0);
}

void glue_close() {
  player_core_close();
}

int64_t glue_position() {
  return player_core_position();
}
