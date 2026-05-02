// tests/fixtures/unsafe_strings.cpp
// Triggers: bugprone-not-null-terminated-result, cppcoreguidelines-pro-bounds-array-to-pointer-decay

#include <cstring>

void copy_short_name(char* dst, const char* src) {
    strncpy(dst, src, 16);                  // may not null-terminate
}

void format_id(char* out) {
    char buf[8];
    strcpy(buf, "id-1234567890");           // overflow + array-to-pointer decay
    memcpy(out, buf, sizeof(buf));
}

int find_char(const char* s, char c) {
    int i = 0;
    while (s[i] != '\0') {                  // bounds-array-to-pointer-decay
        if (s[i] == c) {
            return i;
        }
        ++i;
    }
    return -1;
}
