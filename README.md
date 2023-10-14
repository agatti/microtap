# A TAP producer for MicroPython environments.

## Summary

This is a TAP producer that is meant to be used as part of CI/CD pipelines for
MicroPython projects. This is written with MicroPython's limitations in mind
and its execution environment is not limited to MicroPython's Unix or Windows
ports. That means that with a small bit of hardware (say, a Wifi/UART
controlled relay and a UART-to-WiFi bridge) a CI/CD pipeline can build a test
runner image for a particular device, deploy it on the device itself, and
after performing a device reboot run the test suite. The output can be
captured and be handled by a TAP consumer for further processing or display.
This module follows TAP specification version 14 (it can be found at
https://testanything.org/tap-version-14-specification.html).

## Current limitations

- **Test plans and test points must reside on a filesystem.** Since this
  should also run on MicroPython's Unix and Windows ports, the test plans and
  their test points must reside on a filesystem. This is not usually a problem
  for most MicroPython targets, but if your device does not have enough flash
  space then some modifications of this module are in order. This will be
  addressed in a later version of this module.

- **No nested test plans.** At the moment test plans cannot define their own
  nested sub-plans. Usually this is not a problem as test points and their
  plans are grouped by file. This will be addressed in a later version of this
  module.

- **Test plans can only be in a single directory.** To keep things simple,
  right now the test plans discovery phase does not recurse into directories
  when looking for eligible files. This may not be a limit as MicroPython
  projects do not tend to be that large and therefore the amount of test code
  shouldn't require multiple directories. This will be addressed in a later
  version of this module.

- **No automatic test plan and test points extraction.** Whilst MicroPython
  does allow module contents enumeration, its introspection capabilities are
  limited by design. So for now a manual registration process is used.
  Depending on MicroPython's introspection capabilities this may or may not be
  addressed in a later version of this module.

- **No way to add set up/tear down procedures.** Right now there is no support
  for per-test plan or per-test point set up/tear down procedure. Each test
  point has to sort it itself for the time being. This may or may not be
  addressed in a later version of this module.

- **No test point execution timeout.** Since test points may interact with
  hardware and literally hang the device, tests may have to handle this
  themselves. The main issue here is lack of uniform watchdog support on all
  MicroPython targets (`machine.WDT` only supports six targets at the time of
  writing), and the fact that once the machine watchdog is enabled the
  `machine.WDT` module offers no facility to switch it off. Also, tests may
  dealt with hardware peripherals and do all sorts of weird things to the
  board they run on. For this very reason, all output is not buffered and the
  stream is flushed right after every line. This introduces a bit of latency
  but if the device hangs or reboots or whatever else, the CI/CD will still
  catch up to the last line being output. Right now the best option is to
  configure your CI/CD output parser to detect some text indicating the device
  booted up and some other text indicating the test session ended.

## How to use

`find_test_plans` must be run to check any file named `test_<something>.py` in
the directory it is asked to look into and will return a list of all test plans
it finds in the files it read (*make sure the files do not have dots in them,
or Python's import mechanism will break!*). These test plan instances must then
passed to `execute_test_plans` for them to be run and evaluated. So, in the
simplest case tests can be run with `execute_test_plans(find_test_plans())`.
Now, `execute_test_plans` by default prints the test execution result to
`sys.stdout`; this may not be desirable as for example test code will print
information there that will interfere with the TAP output report. In this
case, a `stream=` argument can be passed to `execute_test_plans` to redirect
the TAP report output to another opened file, or another UART or even a I²C or
SPI bus stream instance.

Test files are loaded and their root level code is executed - this is where
test plans must be defined by calling `build_plan` to build a test plan
container with an optional description (and whether to mark it as skipped).
The returned object (an instance of `Plan`) can then be fed test points via
its `add_test_point` method where functions that take no arguments and return
nothing can be added as test entry points, along with an optional description.
The `Plan` objects that are created this way are automatically extracted by
this module, so they can be discarded right away.

Test points when executed can signal their state to the module by raising four
different exceptions (each exception allows for a message to be passed):

- `Skip`: If a test raises this exception it signals that its execution should
  not be counted due to external circumstances that prevent this test to run,
  and it is assumed as if it was successful (as per TAP specifications).

- `ToDo`: If a test raises this exception it signals that its execution should
  not be counted due to the test itself being incomplete, and it is assumed as
  if it was a failure (as per TAP specifications).

- `Fail`: If a test raises this exception it signals that its execution was
  stopped due to an exception being caught or to an error being detected by
  the test itself, and the test run is marked as a failure.

- `BailOut`: If a test raises this exception it signals that the whole test
  session should be aborted, and no further testing should take place.

The functions and the exceptions mentioned above are injected into the test
file's execution scope, but can also be imported from the `microtap` module to
allow IDE autocompletion support, for other tools perform type checking, and
so on.

## Security notes

The test plan extraction process executes foreign code. If an attacker
takes control of the CI/CD machine or the source code repository then it may
add malicious code in the test files. In the former case (the CI/CD machine
being compromised) tough luck, but if test files are modified there may be a
chance for bad things to happen if this module runs on selected MicroPython
ports (namely Unix, Windows, and anything else with a WiFi/Ethernet interface
being used).

**Use this at your own risk.**

## Licences and copyright

The module is licensed under the MIT licence. A copy of said licence is
available in the repository as `LICENCE.TXT`.

Copyright 2023 Alessandro Gatti - frob.it
