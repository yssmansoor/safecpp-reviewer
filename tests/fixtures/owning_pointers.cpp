// tests/fixtures/owning_pointers.cpp
// Triggers: cppcoreguidelines-owning-memory, cppcoreguidelines-special-member-functions

#include <cstdlib>

class Buffer {
public:
    Buffer(int n) {
        data_ = new int[n];                 // owning raw pointer
        size_ = n;
    }
    ~Buffer() {
        delete[] data_;
    }

    // Missing copy ctor + assignment — special-member-functions

    int* raw() { return data_; }            // exposes ownership

private:
    int* data_;
    int size_;
};

int* make_block(int n) {
    return new int[n];                      // owning_memory
}

void leaky_helper() {
    int* p = (int*)malloc(sizeof(int) * 4); // C-style alloc + cast + leak
    p[0] = 1;
}
