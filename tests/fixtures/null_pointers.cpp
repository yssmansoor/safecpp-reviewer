// tests/fixtures/null_pointers.cpp
// Triggers: modernize-use-nullptr, cppcheck:nullPointer

#include <cstddef>

int read_or_default(int* maybe_value) {
    if (maybe_value == NULL) {     // modernize-use-nullptr
        return 0;
    }
    return *maybe_value;
}

void unsafe_deref() {
    int* p = NULL;                 // modernize-use-nullptr + nullPointer
    *p = 5;                        // cppcheck:nullPointer
}

int* find_first(int* arr, int n) {
    for (int i = 0; i < n; ++i) {
        if (arr[i] == 0) {
            return &arr[i];
        }
    }
    return NULL;                   // modernize-use-nullptr
}
