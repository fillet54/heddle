// Scenario test for the C++ generated from examples/arm_controller.rst.
// Built and run by tests/test_gen.py; it is hand-written, not generated.
#include "arm_controller.hpp"

#include <cstdio>
#include <cstdlib>
#include <cstring>

using components::ArmController;
using State = ArmController::ArmControlState;

static int failures = 0;

static void expect_state(const ArmController& c, State want, const char* when) {
    const char* got = ArmController::to_string(c.arm_control_state());
    if (c.arm_control_state() != want) {
        std::printf("FAIL %s: state %s, expected %s\n", when, got,
                    ArmController::to_string(want));
        ++failures;
    }
}

static void expect_armed(const ArmController& c, bool want, const char* when) {
    if (c.outputs().is_armed != want) {
        std::printf("FAIL %s: is_armed %d, expected %d\n", when, c.outputs().is_armed, want);
        ++failures;
    }
}

// One frame with exactly one input set (or none).
static void frame(ArmController& c, const char* input = nullptr) {
    ArmController::Inputs in;
    if (input) {
        if (!std::strcmp(input, "test_cmd")) in.test_cmd = true;
        else if (!std::strcmp(input, "test_pass")) in.test_pass = true;
        else if (!std::strcmp(input, "test_fail")) in.test_fail = true;
        else if (!std::strcmp(input, "arm_cmd")) in.arm_cmd = true;
        else if (!std::strcmp(input, "disarm_cmd")) in.disarm_cmd = true;
        else if (!std::strcmp(input, "release_done")) in.release_done = true;
        else if (!std::strcmp(input, "stow_cmd")) in.stow_cmd = true;
        else if (!std::strcmp(input, "fault_detected")) in.fault_detected = true;
        else if (!std::strcmp(input, "maint_reset")) in.maint_reset = true;
        else { std::printf("unknown input %s\n", input); std::exit(2); }
    }
    c.step(in);
}

int main() {
    ArmController::Parameters params;
    params.ARM_DELAY = 5;
    ArmController c(params);

    expect_state(c, State::idle, "after construction");
    frame(c);
    expect_state(c, State::idle, "idle with no input");

    // A failed test returns to idle.
    frame(c, "test_cmd");
    expect_state(c, State::testing, "test_cmd");
    frame(c, "test_fail");
    expect_state(c, State::idle, "test_fail");

    // Test, pass, arm.
    frame(c, "test_cmd");
    frame(c, "test_pass");
    expect_state(c, State::standby, "test_pass");
    frame(c, "arm_cmd");
    expect_state(c, State::arming, "arm_cmd");
    expect_armed(c, false, "arming");

    // The delay counts frames spent in arming: ARM_DELAY quiet frames, then the
    // next frame sees arm_timer >= ARM_DELAY and arms.
    for (int i = 0; i < 5; ++i) frame(c);
    expect_state(c, State::arming, "after ARM_DELAY frames");
    frame(c);
    expect_state(c, State::armed, "delay elapsed");
    expect_armed(c, true, "entry action of armed");

    // Disarm runs the exit action.
    frame(c, "disarm_cmd");
    expect_state(c, State::standby, "disarm_cmd");
    expect_armed(c, false, "exit action of armed");

    // A repeated arm command restarts the delay (self-transition re-enters).
    frame(c, "arm_cmd");
    for (int i = 0; i < 4; ++i) frame(c);
    frame(c, "arm_cmd");
    for (int i = 0; i < 4; ++i) frame(c);
    expect_state(c, State::arming, "delay restarted");
    frame(c);
    frame(c);
    expect_state(c, State::armed, "restarted delay elapsed");

    // A fault wins from any state, runs exit actions, and latches.
    frame(c, "fault_detected");
    expect_state(c, State::fault, "fault_detected");
    expect_armed(c, false, "exit action on fault");
    frame(c, "arm_cmd");
    expect_state(c, State::fault, "fault is latched");
    frame(c, "maint_reset");
    expect_state(c, State::idle, "maint_reset");

    // Priority: a fault and a test command in the same frame -> fault (0) wins.
    ArmController::Inputs both;
    both.fault_detected = true;
    both.test_cmd = true;
    c.step(both);
    expect_state(c, State::fault, "priority 0 beats priority 1");

    // reset() returns to the initial state.
    c.reset();
    expect_state(c, State::idle, "reset");

    if (failures) {
        std::printf("%d failure(s)\n", failures);
        return 1;
    }
    std::printf("arm controller scenario: all checks passed\n");
    return 0;
}
