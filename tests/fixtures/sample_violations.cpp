// tests/fixtures/sample_violations.cpp
// Synthetic file with deliberate violations for baseline testing.
// DO NOT run in production — this file is intentionally bad C++.

#include <cstdlib>
#include <cstring>

// [cppcoreguidelines-pro-type-cstyle-cast] C-style cast
// [modernize-use-nullptr] NULL instead of nullptr
int process(int a, int b) {
    int* ptr = NULL;                   // modernize-use-nullptr
    int result = (int)(a + b);         // cppcoreguidelines-pro-type-cstyle-cast

    // [cppcheck: nullPointer] — ptr is never assigned a valid address
    *ptr = result;

    return result;
}

// [cppcoreguidelines-avoid-magic-numbers]
double magic() {
    return 3.14159 * 42;
}

// [modernize-use-override] missing override keyword
class Base {
public:
    virtual void tick() {}
    virtual ~Base() = default;
};

class Derived : public Base {
public:
    void tick() {}   // should be tick() override
};

// [cppcheck: unusedFunction]
static void unused_helper() {
    char buf[16];
    strcpy(buf, "hello world!!!");  // buffer overflow + deprecated strcpy
}
