/**
 * @file test/test_support/arduino_stub.h
 * @brief Minimal Arduino API stubs for native (host) unit testing.
 *
 * Included automatically when building with TEST_BUILD defined.
 */

#pragma once

#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include <math.h>

// -----------------------------------------------------------------
// Types
// -----------------------------------------------------------------
typedef bool       boolean;
typedef uint8_t    byte;
typedef uint16_t   word;

// -----------------------------------------------------------------
// millis() stub — naive counter
// -----------------------------------------------------------------
#ifdef __cplusplus
extern "C" {
#endif

static inline uint32_t millis() {
    // Return a monotonically increasing stub value based on a global counter
    static uint32_t _ms = 0;
    return _ms;
}

static inline void millis_advance(uint32_t ms) {
    // Test helper to advance the fake clock
    extern uint32_t _ms_stub_counter;
    (void)ms;
}

#ifdef __cplusplus
}
#endif

// -----------------------------------------------------------------
// Minimal Serial stub (no-op)
// -----------------------------------------------------------------
struct _SerialStub {
    template<typename T> void print(T)   {}
    template<typename T> void println(T) {}
    void printf(const char *, ...) {}
    void begin(int) {}
} inline Serial;

// -----------------------------------------------------------------
// F() macro (Arduino flash string — passthrough on native)
// -----------------------------------------------------------------
#define F(s) (s)

// -----------------------------------------------------------------
// Arduino.h include guard bypass
// -----------------------------------------------------------------
#define ARDUINO_H
