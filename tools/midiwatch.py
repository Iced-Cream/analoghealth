# SPDX-License-Identifier: GPL-2.0-or-later
"""A time limit on winmm calls (used by winmidi.py).

A winmm call goes to the Windows MIDI service, and when that service is stuck
(seen 2026-11-05 after a flash through a USB MIDI mirror: every later port open
hung until the mirror's cable was replugged) the call never returns. Python
cannot interrupt a foreign call, so each one runs on a worker thread and the
caller waits for it with a time limit. A call that runs over raises Stuck and
leaves the watchdog stuck: every later call fails at once, since it would only
queue behind the hung one, and the process should end with hard_exit."""
import os
import queue
import sys
import threading

DEFAULT_TIMEOUT = 10.0
STUCK_EXIT = 4          # exit code of a run that ended because a MIDI call hung
ADVICE = ('close Transfer and other MIDI programs; if the port still hangs, replug the USB cable '
          'of the unit (or of the hub or mirror it is behind) and try again')


class Stuck(OSError):
    """A winmm call did not return in time."""

    def __init__(self, call, seconds, message):
        super().__init__(message)
        self.call, self.seconds = call, seconds


def timeout_from_env(name='ANALOGRUN_WINMM_TIMEOUT', default=DEFAULT_TIMEOUT):
    raw = os.environ.get(name, '').strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        raise SystemExit('%s must be a number of seconds, not %r' % (name, raw))
    if value <= 0:
        raise SystemExit('%s must be more than 0' % name)
    return value


class Watchdog:
    def __init__(self, timeout=DEFAULT_TIMEOUT):
        self.timeout = timeout
        self.stuck = None           # the call that did not return
        self._jobs = queue.Queue()
        self._worker = None
        self._lock = threading.Lock()

    def _run(self):
        while True:
            fn, args, box, done = self._jobs.get()
            try:
                box.append((True, fn(*args)))
            except BaseException as e:      # noqa: BLE001 - handed back to the caller
                box.append((False, e))
            done.set()

    def call(self, name, fn, *args, timeout=None):
        """fn(*args), or Stuck if it has not returned in `timeout` seconds."""
        limit = self.timeout if timeout is None else timeout
        with self._lock:
            if self.stuck:
                raise Stuck(self.stuck, 0.0, '%s not called: %s is still stuck' % (name, self.stuck))
            if self._worker is None:
                self._worker = threading.Thread(target=self._run, name='winmm-watchdog', daemon=True)
                self._worker.start()
            box, done = [], threading.Event()
            self._jobs.put((fn, args, box, done))
            if not done.wait(limit):
                self.stuck = name
                raise Stuck(name, limit, 'Windows MIDI did not return from %s in %g s: the MIDI '
                            'service looks stuck; %s' % (name, limit, ADVICE))
        ok, value = box[0]
        if ok:
            return value
        raise value


WATCHDOG = Watchdog(timeout_from_env())


def stuck():
    """The winmm call that hung in this process, or None."""
    return WATCHDOG.stuck


def hard_exit(code):
    """End the process at once, without the interpreter's shutdown or the DLLs'
    unload code, either of which can wait on the stuck call."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:               # noqa: BLE001
            pass
    if sys.platform == 'win32':
        import ctypes
        k32 = ctypes.WinDLL('kernel32')
        k32.GetCurrentProcess.restype = ctypes.c_void_p
        k32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        k32.TerminateProcess(k32.GetCurrentProcess(), code)
    os._exit(code)


def exit_if_stuck(code):
    """For a main(): `code`, or hard_exit with STUCK_EXIT after a hung call."""
    if WATCHDOG.stuck:
        hard_exit(STUCK_EXIT)
    return code
