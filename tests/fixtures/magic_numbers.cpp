// tests/fixtures/magic_numbers.cpp
// Triggers: cppcoreguidelines-avoid-magic-numbers, readability-magic-numbers

double area_of_circle(double radius) {
    return 3.14159265 * radius * radius;
}

int seconds_in_a_day() {
    return 86400;
}

double tax_total(double price) {
    return price * 1.075;
}

int retry_count() {
    return 5;
}
