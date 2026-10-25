// Logging for every client: a printf-style function in C.
#include <stdarg.h>
#include <stdio.h>

void player_log(const char *format, ...) {
  va_list args;
  va_start(args, format);
  vfprintf(stderr, format, args);
  va_end(args);
}
