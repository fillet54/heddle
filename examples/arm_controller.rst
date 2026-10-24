==============
Arm Controller
==============

:component: ArmController
:rate: 50 Hz
:layer: application

.. default-role:: sym

Arms the release mechanism on operator command, but only after a passing
self test. Arming takes `ARM_DELAY` frames so the crew can abort. Any
detected fault latches the controller in `fault` until a maintenance reset.

Inputs
======

test_cmd : bool
   Operator request to run the self test.

test_pass : bool
   Self test finished with no failures.

test_fail : bool
   Self test finished with at least one failure.

arm_cmd : bool
   Operator arm request.

disarm_cmd : bool
   Operator disarm request; always honoured.

release_done : bool
   The release mechanism reports a completed release.

stow_cmd : bool
   Operator request to stow the system.

fault_detected : bool
   Any monitored fault from the health monitor.

maint_reset : bool
   Maintenance reset discrete.

Outputs
=======

is_armed : bool
   True only while the controller is in `armed`.

Parameters
==========

ARM_DELAY : frames
   Frames between an accepted arm command and the armed state.

   :value: 100

State
=====

arm_timer : frames
   Frames spent in `arming`.

   :reset: 0

Procedures
==========

Arm Control
-----------

The arm control state machine. A crew member must test, then arm; arming
can be aborted at any time before it completes, and a fault from any state
wins over every other transition.

States
^^^^^^

idle
   Stowed and safe. The initial state.

testing
   Running the self test.

standby
   Tested and ready to arm. Outputs safe.

arming
   Arming sequence running; the crew can still abort.

   :entry: arm_timer = 0
   :during: arm_timer += 1

armed
   Armed. Drives `is_armed` true.

   :entry: is_armed = true
   :exit: is_armed = false

fault
   Latched until a maintenance reset.

Transitions
^^^^^^^^^^^

idle → testing : 1
   The crew starts the self test.

   :guard: test_cmd

testing → standby : 1
   A passing self test makes the system ready to arm.

   :guard: test_pass

testing → idle : 2
   A failed self test returns to idle; the crew must retest.

   :guard: test_fail

standby → arming : 1
   An arm command starts the arming delay.

   :guard: arm_cmd

standby → idle : 2
   The crew can stow from standby.

   :guard: stow_cmd

arming → standby : 1
   A disarm during arming aborts it.

   :guard: disarm_cmd

arming → armed : 2
   The arming delay has elapsed.

   :guard: arm_timer >= ARM_DELAY

arming → arming : 3
   A repeated arm command restarts the delay.

   :guard: arm_cmd
   :action: arm_timer = 0

armed → standby : 1
   A disarm returns to standby.

   :guard: disarm_cmd

armed → standby : 2
   A completed release also returns to standby.

   :guard: release_done

any → fault : 0
   A detected fault wins over every other transition, from every state.

   :guard: fault_detected

fault → idle : 1
   Only a maintenance reset clears the latch.

   :guard: maint_reset
