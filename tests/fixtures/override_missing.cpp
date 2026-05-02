// tests/fixtures/override_missing.cpp
// Triggers: modernize-use-override, cppcoreguidelines-special-member-functions

class Sensor {
public:
    virtual ~Sensor() = default;
    virtual void read() {}
    virtual int sample() { return 0; }
};

class Lidar : public Sensor {
public:
    void read() {}        // missing override
    int sample() {        // missing override
        return 42;
    }
};

class Radar : public Sensor {
public:
    virtual void read() {}    // missing override
};
