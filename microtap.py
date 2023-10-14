#
# Copyright (C) 2023 Alessandro Gatti - Frob.it
#
# SPDX-License-Identifier: MIT
#
# Version 0.1.0
#

"""A TAP producer for MicroPython environments.

Summary
-------

This is a TAP producer that is meant to be used as part of CI/CD pipelines for
MicroPython projects.  This is written with MicroPython's limitations in mind
and its execution environment is not limited to MicroPython's Unix or Windows
ports.  That means that with a small bit of hardware (say, a Wifi/UART
controlled relay and a UART-to-WiFi bridge) a CI/CD pipeline can build a test
runner image for a particular device, deploy it on the device itself, and
after performing a device reboot run the test suite.  The output can be
captured and be handled by a TAP consumer for further processing or display.
This module follows TAP specification version 14 (it can be found at
https://testanything.org/tap-version-14-specification.html).

Current limitations
-------------------

- Test plans and test points must reside on a filesystem

  Since this should also run on MicroPython's Unix and Windows ports, the test
  plans and their test points must reside on a filesystem.  This is not
  usually a problem for most MicroPython targets, but if your device does not
  have enough flash space then some modifications of this module are in order.
  This will be addressed in a later version of this module.

- No nested test plans.

  At the moment test plans cannot define their own nested sub-plans.  Usually
  this is not a problem as test points and their plans are grouped by file.
  This will be addressed in a later version of this module.

- Test plans can only be in a single directory.

  To keep things simple, right now the test plans discovery phase does not
  recurse into directories when looking for eligible files.  This may not be a
  limit as MicroPython projects do not tend to be that large and therefore the
  amount of test code shouldn't require multiple directories.  This will be
  addressed in a later version of this module.

- No automatic test plan and test points extraction.

  Whilst MicroPython does allow module contents enumeration, its introspection
  capabilities are limited by design.  So for now a manual registration
  process is used.  Depending on MicroPython's introspection capabilities this
  may or may not be addressed in a later version of this module.

- No way to add set up/tear down procedures.

  Right now there is no support for per-test plan or per-test point
  set up/tear down procedure.  Each test point has to sort it itself for the
  time being.  This may or may not be addressed in a later version of this
  module.

- No test point execution timeout.

  Since test points may interact with hardware and literally hang the device,
  tests may have to handle this themselves.  The main issue here is lack of
  uniform watchdog support on all MicroPython targets (`machine.WDT` only
  supports six targets at the time of writing), and the fact that once the
  machine watchdog is enabled the `machine.WDT` module offers no facility to
  switch it off.  Also, tests may dealt with hardware peripherals and do all
  sorts of weird things to the board they run on.  For this very reason, all
  output is not buffered and the stream is flushed right after every line.
  This introduces a bit of latency but if the device hangs or reboots or
  whatever else, the CI/CD will still catch up to the last line being output.
  Right now the best option is to configure your CI/CD output parser to detect
  some text indicating the device booted up and some other text indicating the
  test session ended.

How to use
----------

`find_test_plans` must be run to check any file named `test_<something>.py` in
the directory it is asked to look into and will return a list of all test plans
it finds in the files it read.  These test plan instances must then passed to
`execute_test_plans` for them to be run and evaluated.  So, in the simplest
case tests can be run with `execute_test_plans(find_test_plans())`.  Now,
`execute_test_plans` by default prints the test execution result to
`sys.stdout`; this may not be desirable as for example test code will print
information there that will interfere with the TAP output report.  In this
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

- Skip

  If a test raises this exception it signals that its execution should not be
  counted due to external circumstances that prevent this test to run, and it
  is assumed as if it was successful (as per TAP specifications).

- ToDo

  If a test raises this exception it signals that its execution should not be
  counted due to the test itself being incomplete, and it is assumed as if it
  was a failure (as per TAP specifications).

- Fail

  If a test raises this exception it signals that its execution was stopped
  due to an exception being caught or to an error being detected by the test
  itself, and the test run is marked as a failure.

- BailOut

  If a test raises this exception it signals that the whole test session
  should be aborted, and no further testing should take place.

The functions and the exceptions mentioned above are injected into the test
file's execution scope, but can also be imported from the `microtap` module to
allow IDE autocompletion support, for other tools perform type checking, and
so on.

Security notes
--------------

The test plan extraction process executes foreign code.  If an attacker
takes control of the CI/CD machine or the source code repository then it may
add malicious code in the test files.  In the former case (the CI/CD machine
being compromised) tough luck, but if test files are modified there may be a
chance for bad things to happen if this module runs on selected MicroPython
ports (namely Unix, Windows, and anything else with a WiFi/Ethernet interface
being used).

**Use this at your own risk.**

## Licences and copyright

The module is licensed under the MIT licence. A copy of said licence is
available in the repository as `LICENCE.TXT`.
"""

import io
import os
import sys

try:
    # noinspection PyUnresolvedReferences
    from typing import (
        Callable,
        Dict,
        List,
        Literal,
        Optional,
        Sequence,
        TextIO,
        Tuple,
        Union,
    )
except ImportError:
    pass


def _escape_string(string: Optional[str]) -> Optional[str]:
    """Escape the given string according to the TAP comment escaping rules.

    Parameters
    ----------
    string : str, optional
        The string to escape.

    Returns
    -------
    str, optional
        The escaped string, or `None` if the input string was either `None`
        as well or if the output string is empty after removing leading and
        trailing whitespace.

    See Also
    --------
    TAP version 14 specification, "Escaping" section.
    """

    if not (string and string.strip()):
        return None

    return string.replace("\\", "\\\\").replace("#", "\\#").strip()


def _trim_empty_to_none(string: Optional[str]) -> Optional[str]:
    """Remove whitespace from a string, returning `None` if it ends up blank.

    Parameters
    ----------
    string : str, optional
        The string to remove whitespace from, if it is not None.

    Returns
    -------
    str, optiona;
        A string with its leading and trailing whitespace removed if it was
        not `None` to begin with and if it is not blank after the whitespace
        removal process, `None` otherwise.
    """

    if not (string and string.strip()):
        return None

    return string.strip()


class Skip(Exception):
    """Exception thrown by a test to mark itself as to be skipped."""


class ToDo(Exception):
    """Exception thrown by a test to mark itself as not yet finished."""


class Fail(Exception):
    """Exception thrown by a test to mark itself as failed."""


class BailOut(Exception):
    """Exception thrown by a test to end the testing session.

    See Also
    --------
    TAP version 14 specification, "Bail Out!" section.
    """


class Plan:
    # noinspection PyUnresolvedReferences
    """Test plan container.

    Attributes
    ----------
    file_name : str
        The file name this test plan is bound to.
    description : str, optional
        An optional human-readable description of what this test plan
        entails.  If this plan is meant to be skipped, it will also be the
        reason for said outcome.
    skipped : bool
        A flag indicating whether this plan should be skipped or not.
    test_cases : Sequence[Sequence[Callable[[], None], Optional[str]]]
        A sequence of tuples made up test point functions to invoke and
        their associate description (if one is present).
    """

    def __init__(
        self, file_name: str, description: Optional[str] = None, skipped: bool = False
    ) -> None:
        """Create an instance of a MicroTap test plan.

        Parameters
        ----------
        file_name : str
            The file name this plan is associated to.
        description : str, optional
            An optional human-readable description of what this test plan
            entails.  If this plan is meant to be skipped, it will also be the
            reason for said outcome.
        skipped : bool
            A flag indicating whether this plan should be skipped or not.
        """

        self._file_name = file_name
        self._description = _trim_empty_to_none(description)
        self._test_points = []  # type: ignore[var-annotated]
        self._skipped = skipped

    def add_test_point(
        self, test_point: Callable[[], None], description: Optional[str] = None
    ) -> None:
        """Add the given test point function to the test plan.

        Parameters
        ----------
        test_point : Callable[[], None]
            A function that takes no arguments and returns nothing acting as
            the basic unit of testing, that will be invoked and evaluated
            when executing the test plan.
        description : str, optional
            An optional human-readable description of the test point.

        Notes
        -----
        No efforts are made to prevent adding the same test point more than
        once.  Making sure this does not happen is up to the test plan writer.
        """

        self._test_points.append((test_point, _trim_empty_to_none(description)))

    @property
    def file_name(self) -> str:
        """Return the name of the file this test plan is bound to.

        Returns
        -------
        str
            The name of the file this test plan is bound to.
        """

        return self._file_name

    @property
    def description(self) -> Optional[str]:
        """Return the human-readable description set for this test plan.

        Returns
        -------
        str, optional
            The human-readable description set for this test plan.
        """

        return self._description

    @property
    def test_points(self) -> Sequence[Tuple[Callable[[], None], Optional[str]]]:
        """Return the registered test points.

        Returns
        -------
        Sequence[Tuple[Callable[[], None], Optional[str]]]
            The registered test points.
        """

        return self._test_points

    @property
    def skipped(self) -> bool:
        """Return a flag indicating whether this plan should be skipped.

        Returns
        -------
        bool
            A flag indicating whether this plan should be skipped.
        """

        return self._skipped


# The test plans container.
_test_plans: List[Plan] = []

# The name of the file being processed.
_current_file_name: Optional[str] = None


# A global variable for the current file name is used to avoid having extra
# "hidden" parameters to `register_plan` that are pre-filled via
# `functools.partial`.  The rationale is to allow test files to import public
# entries from the `microtap` module to let an IDE or other tools perform
# type checking, without giving the user the chance to mess things up by
# setting parameters that aren't meant to be exposed in the first place.


def build_plan(description: Optional[str] = None, skipped: bool = False) -> Plan:
    """Build a test plan bound to the currently handled file.

    This function will also add the built plan object to the global
    `_test_plans` variable (which is not in the function caller's global
    scope).

    Parameters
    ----------
    description : str, optional
        An optional human-readable description of what this test plan
        entails.  If this plan is meant to be skipped, it will also be the
        reason for said outcome.
    skipped
        A flag indicating whether this plan should be skipped or not.

    Returns
    -------
    Plan
        An instance of `Plan` bound to the current file and with the given
        parameters, to be used for test points registration.
    """

    assert _current_file_name
    plan = Plan(_current_file_name, description=description, skipped=skipped)
    _test_plans.append(plan)
    return plan


def find_test_plans(path: str = os.getcwd()) -> Sequence[Plan]:
    """Find test plans in test files present in the given directory.

    This function will look for regular python files whose name starts with
    `test_`, and will load each file into the global interpreter scope.  Make
    sure test files do not have any dots in them, as otherwise Python's import
    mechanism will break.

    This means that root level code in test files will be executed, and in
    that execution scope the following entities are injected:

        - `build_plan`, which is a function that allows to create a test
          plan.  This returns an instance of `Plan`, which allows to add
          tests to the plan for later evaluation.
        - `Skip`, which is an exception that tests must raise to mark their
          execution as skipped by the test runner.
        - `ToDo`, which is an exception that tests must raise to mark their
          incompleteness to the test runner.
        - `Fail`, which is an exception that tests must raise to mark their
          failure as internally detected.
        - `BailOut`, which is an exception that tests must raise to mark the
          end of the testing session as an impossibility to continue was
          detected.

    Test files must call `register_plan` to build a test plan bound to the
    file, and subsequently call `add_test_point` on the returned `Plan`
    object to add test points.  Whilst MicroPython does allow module contents
    enumeration, its introspection capabilities are limited by design.  So
    for now a manual registration process is used - this may change in the
    future once MicroPython's introspection capabilities are investigated in
    depth.

    Registered test points will be called with no arguments, and if a extra
    set up or tear down steps are required, the test point itself must handle
    it on its own for now.

    This function should be robust enough when it comes to parsing external
    files, although it will raise exceptions if filesystem operations fail.

    See Also
    --------
    Plan

    Notes
    -----
    This function currently does not iterate through directories, mostly to
    avoid having duplicate test file names and to keep the implementation
    simple.  This is probably going to change in the next version.

    Parameters
    ----------
    path : str, optional
        The filesystem path to query for test files, defaulting to the current
        directory.

    Returns
    -------
    Sequence[Plan]
        A sequence of `Plan` instances extracted from the test files being
        processed.  If no valid files were found or if no test plans were
        collected it will return an empty sequence.
    """

    global _current_file_name, _test_plans

    _test_plans = []
    for name_tuple in os.ilistdir(path):  # type: ignore[attr-defined]
        file_name = name_tuple[0]
        file_type = name_tuple[1]

        if (
            not file_name.startswith("test_")
            or not file_name.endswith(".py")
            or file_type != 0x8000
        ):
            continue

        module_name = file_name[:-3]
        # noinspection PyBroadException
        try:
            _current_file_name = file_name
            exec(
                f"import {module_name}",
                {
                    "build_plan": build_plan,
                    "Plan": Plan,
                    "Fail": Fail,
                    "Skip": Skip,
                    "ToDo": ToDo,
                    "BailOut": BailOut,
                },
            )
        except:
            # This is done on purpose, as files may have syntax errors or any
            # other kind of nastiness inside.
            pass
    _current_file_name = None
    return _test_plans


def _format_exception(exception: BaseException) -> str:
    """Format an exception to be part of a YAML diagnostics block.

    Parameters
    ----------
    exception : BaseException
        The exception (or exception-like) object to format as an unhandled
        error in a test.

    Returns
    -------
    str
        A string containing the exception formatted as a YAML diagnostics
        block, to be output along the result of a test.

    See Also
    --------
    TAP version 14 specification, "YAML Diagnostics" section.
    """

    with io.StringIO() as stream:
        stream.write("---\n")
        stream.write(f"exception: {' '.join(exception.args)}\n")
        stream.write(f"traceback: |-\n")
        with io.StringIO() as exception_details:
            sys.print_exception(exception, exception_details)  # type: ignore[attr-defined]
            for line in exception_details.getvalue().splitlines():
                stream.write(f"  {line}\n")
        stream.write("...\n")
        return stream.getvalue()


def _write_test_result(
    stream_writer: Callable[[str], None],
    description: Optional[str],
    index: int,
    success: bool,
    directive: Optional[Union[Literal["SKIP"], Literal["TODO"]]] = None,
    directive_description: Optional[str] = None,
) -> None:
    """Write a test point execution result to the given stream.

    Parameters
    ----------
    stream_writer : Callable[[str], None]
        A function that will write the given string to the appropriate output
        stream.
    description : str
        An optional human-readable description for the test point.
    index : int
        The sequential number of the test point in the test plan.
    success : bool
        A flag indicating whether the test point execution should be
        considered a success or a failure.
    directive : str, optional
        An optional directive indicating a special condition.  For now, this
        follows the TAP specification allowing only SKIP and TODO directives.
    directive_description : str, optional
        An optional human-readable description of what the directive entails.
        This can be set even if no directive is provided, in which case this
        string will be ignored.

    See Also
    --------
    TAP version 14 specification, "Test Points" section.
    """

    string = "ok" if success else "not ok"
    string += f" {index}"
    if description:
        string += f" - {description}"
    if directive:
        string += f" # {directive}"
        if escaped_directive_description := _escape_string(directive_description):
            string += f" {escaped_directive_description}"
    stream_writer(string)


def _execute_test_plan(plan: Plan, stream_writer: Callable[[str], None]) -> bool:
    """Execute the given test plan.

    Parameters
    ----------
    plan : Plan
        The test plan to execute.
    stream_writer : Callable[[str], None]
        A function that will write the given string to the appropriate output
        stream.

    Returns
    -------
    bool
        A flag indicating whether one or more test plans failed.
    """

    if not plan.test_points or plan.skipped:
        header = "1..0"
        if plan.description:
            header += f" # SKIP {_escape_string(plan.description)}"
        stream_writer(header)
        return True

    success = True
    stream_writer(f"1..{len(plan.test_points)}")
    for index, test in enumerate(plan.test_points, start=1):
        test_function, test_description = test
        # noinspection PyBroadException
        try:
            test_function()
            _write_test_result(stream_writer, test_description, index, success=True)
        except Skip as exception:
            _write_test_result(
                stream_writer,
                test_description,
                index,
                success=True,
                directive="SKIP",
                directive_description=" ".join(exception.args),
            )
        except ToDo as exception:
            _write_test_result(
                stream_writer,
                test_description,
                index,
                success=False,
                directive="TODO",
                directive_description=" ".join(exception.args),
            )
        except Fail:
            _write_test_result(stream_writer, test_description, index, success=False)
            success = False
        except BailOut:
            raise
        except Exception as exception:
            _write_test_result(stream_writer, test_description, index, success=False)
            for line in _format_exception(exception).splitlines():
                stream_writer(f"  {line.rstrip()}")
            success = False
    return success


def _build_stream_writer(
    stream: TextIO, indentation_level: int = 0
) -> Callable[[str], None]:
    """Build a function to write to a stream using a given indentation level.

    Parameters
    ----------
    stream : TextIO
        The stream to write text to.
    indentation_level : int, optional
        The indentation level to apply to each line being printed.

    Returns
    -------
    Callable[[str], None]
        A function that when invoked will write the given text to the selected
        stream, optionally adding a fixed number of spaces before each line
        being writen.
    """

    prefix = " " * indentation_level

    def _writer(string: str) -> None:
        stream.write(f"{prefix}{string}\n")
        stream.flush()

    return _writer


def _format_bailout_exception(exception: BailOut) -> str:
    """Format the given bail out exception.

    Parameters
    ----------
    exception : BailOut
        A BailOut exception raised by a test being run, indicating the test
        session should end right there.

    Returns
    -------
    str
        A string with the properly formatted bailing out message.

    See Also
    --------
    TAP version 14 specification, "Bail Out!" section.
    """

    return f"Bail out! {_escape_string(' '.join(exception.args))}".strip() + "\n"


def execute_test_plans(
    plans: Union[Plan, Sequence[Plan]],
    output_stream: TextIO = sys.stdout,
    root_plan: bool = True,
) -> None:
    """Execute the given test plans.

    If more than a plan is provided, each plan will be handled as if it
    were a sub-plan of the root plan (which contains no test points).

    Parameters
    ----------
    plans : Union[Plan, Sequence[Plan]]
        The plans to execute.  This does not have to be a Sequence, but can
        also be a single Plan instance.
    output_stream : TextIO, optional
        An optional text stream to write the result to.  If not passed, it
        defaults to `sys.stdout`.
    root_plan : bool, optional
        An optional flag indicating whether a fake root plan should be
        generated if more than one plans are to be executed.  This is here to
        accommodate viewers like `tap.py`, which cannot handle no root plan
        with one or more sub-plans.  `tapview` handles the lack of root plan
        just fine for example, so this requires a bit of trial and error.
        The TAP specification version 14 does not seem to indicate this is a
        possible case so here we are...
    """

    plan = None
    single_plan = False
    if type(plans) is Plan:
        single_plan = True
        plan = plans
    elif type(plans) in (list, tuple):
        if len(plans) == 1:  # type: ignore[arg-type]
            single_plan = True
            plan = plans[0]  # type: ignore[index]

    writer = _build_stream_writer(output_stream)
    writer("TAP version 14")

    if single_plan:
        try:
            _execute_test_plan(plan, writer)  # type: ignore[arg-type]
        except BailOut as exception:
            writer(_format_bailout_exception(exception))
        return

    if root_plan:
        writer(f"1..{len(plans)}")  # type: ignore[arg-type]
    results = []
    quit_early = False
    for plan in plans:  # type: ignore[union-attr]
        try:
            writer(f"# Tests for {plan.file_name}")
            success = _execute_test_plan(plan, _build_stream_writer(output_stream, 4))
            results.append(success)
        except BailOut as exception:
            writer(_format_bailout_exception(exception))
            quit_early = True
        if quit_early:
            return

    if root_plan:
        for index, plan_result in enumerate(zip(plans, results), start=1):  # type: ignore[arg-type]
            plan, success = plan_result
            result = "ok" if success else "not ok"
            result += f" {index}"
            if plan.description:
                result += f" - {plan.description}"
            writer(result)
