#include <iostream>

int main() {
    int* ptr = new int(5);  // raw pointer
    std::cout << *ptr << std::endl;
    return 0;
}
