// tests/fixtures/cstyle_casts.cpp
// Triggers: cppcoreguidelines-pro-type-cstyle-cast, modernize-use-nullptr
#include <cstdint>

float to_float(int n) {
    return (float)n;          // C-style cast
}

int* legacy_alloc() {
    return (int*)NULL;        // C-style cast + NULL
}

double scale(int x, int y) {
    return ((double)x) * ((double)y);    // multiple C-style casts
}

uint8_t mask_byte(int v) {
    return (uint8_t)(v & 0xFF);
}
