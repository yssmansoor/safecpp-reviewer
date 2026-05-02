// tests/fixtures/reinterpret_casts.cpp
// Triggers: cppcoreguidelines-pro-type-reinterpret-cast

#include <cstdint>
#include <cstring>

float bits_to_float(uint32_t bits) {
    return *reinterpret_cast<float*>(&bits);  // reinterpret_cast — undefined behaviour
}

uint32_t float_to_bits(float f) {
    return *reinterpret_cast<uint32_t*>(&f);  // reinterpret_cast — undefined behaviour
}

struct Header {
    uint32_t magic;
    uint16_t version;
};

const Header* parse_header(const char* buffer) {
    return reinterpret_cast<const Header*>(buffer);
}
