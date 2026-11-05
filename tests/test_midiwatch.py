#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""midiwatch.py and the watchdog on winmidi.py's winmm calls, with a fake winmm so
that it runs on any OS."""
from pathlib import Path
from unittest import mock
import ctypes
import importlib
import subprocess
import sys
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import midiwatch


class FakeDLL:
    """winmm stand-in: every function returns 0 unless `hang` names it, which then
    blocks until `release` is set."""

    def __init__(self):
        self.hang, self.calls, self.release = set(), [], threading.Event()

    def __getattr__(self, name):
        if name.startswith('__'):
            raise AttributeError(name)

        def fn(*args):
            self.calls.append(name)
            if name in self.hang:
                self.release.wait()
            return 0
        object.__setattr__(self, name, fn)
        return fn


class WatchdogTest(unittest.TestCase):
    def test_returns_and_raises_like_the_call(self):
        w = midiwatch.Watchdog(1.0)
        self.assertEqual(w.call('add', lambda a, b: a + b, 2, 3), 5)
        with self.assertRaises(KeyError):
            w.call('get', {}.__getitem__, 'x')
        self.assertIsNone(w.stuck)

    def test_a_hung_call_fails_in_time_and_blocks_the_rest(self):
        w, never = midiwatch.Watchdog(0.2), threading.Event()
        t0 = time.monotonic()
        with self.assertRaises(midiwatch.Stuck) as e:
            w.call('midiInOpen', never.wait)
        self.assertLess(time.monotonic() - t0, 1.0)
        self.assertIsInstance(e.exception, OSError)      # analogrun's OSError paths see it
        self.assertEqual(w.stuck, 'midiInOpen')
        ran = []
        with self.assertRaises(midiwatch.Stuck):
            w.call('midiOutClose', ran.append, 1)
        self.assertEqual(ran, [])
        never.set()


class WinmidiTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeDLL()
        # off Windows there is no WinDLL or WINFUNCTYPE: the fake DLL and CFUNCTYPE stand in
        with mock.patch.object(ctypes, 'WinDLL', lambda name, **kw: self.fake, create=True), \
                mock.patch.object(ctypes, 'WINFUNCTYPE', getattr(ctypes, 'WINFUNCTYPE', ctypes.CFUNCTYPE),
                                  create=True):
            sys.modules.pop('winmidi', None)
            self.winmidi = importlib.import_module('winmidi')
        self.saved = midiwatch.WATCHDOG
        midiwatch.WATCHDOG = midiwatch.Watchdog(0.2)
        # the fake has no ports: name one pair
        self.find = mock.patch.object(self.winmidi, 'find', lambda name=None: (0, 0))
        self.find.start()

    def tearDown(self):
        self.find.stop()
        self.fake.release.set()
        midiwatch.WATCHDOG = self.saved
        sys.modules.pop('winmidi', None)

    def test_open_that_hangs(self):
        self.fake.hang.add('midiOutOpen')
        t0 = time.monotonic()
        with self.assertRaises(midiwatch.Stuck) as e:
            self.winmidi.Port('Analog Rytm')
        self.assertLess(time.monotonic() - t0, 0.2 * self.winmidi.OPEN_FACTOR + 0.5)
        self.assertEqual(e.exception.call, 'midiOutOpen')
        self.assertNotIn('midiInClose', self.fake.calls)     # nothing more after a hung call

    def opened(self):
        p = self.winmidi.Port('Analog Rytm')
        p.hin, p.hout = self.winmidi.wintypes.HANDLE(1), self.winmidi.wintypes.HANDLE(2)
        return p          # the fake opens write no handle

    def test_short_open_that_hangs(self):
        self.fake.hang.add('midiInOpen')
        with self.assertRaises(midiwatch.Stuck) as e:
            self.winmidi.Port('Analog Rytm', short=True)
        self.assertEqual(e.exception.call, 'midiInOpen')

    def test_clean_close(self):
        self.opened().close()
        self.assertIn('midiInClose', self.fake.calls)
        self.assertIn('midiOutClose', self.fake.calls)

    def test_send_that_hangs_then_close(self):
        p = self.opened()
        self.fake.hang.add('midiOutLongMsg')
        with self.assertRaises(midiwatch.Stuck):
            p.send(b'\xf0\x7e\x7f\x06\x01\xf7')
        p.close()
        self.assertNotIn('midiOutClose', self.fake.calls)

    def test_listing_that_hangs(self):
        self.find.stop()
        self.fake.hang.add('midiInGetNumDevs')
        with self.assertRaises(midiwatch.Stuck):
            self.winmidi.find('Analog Rytm')
        self.find.start()


class ExitTest(unittest.TestCase):
    def test_a_stuck_process_ends_with_exit_code_4(self):
        # a non-daemon thread would keep a normal exit waiting; hard_exit does not wait
        code = ('import sys, threading; sys.path.insert(0, %r); import midiwatch\n'
                'threading.Thread(target=threading.Event().wait).start()\n'
                'w = midiwatch.WATCHDOG = midiwatch.Watchdog(0.1)\n'
                'try:\n    w.call("midiInOpen", threading.Event().wait)\n'
                'except midiwatch.Stuck as e:\n    print(e)\n'
                'sys.exit(midiwatch.exit_if_stuck(0))\n' % str(ROOT / 'tools'))
        r = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, midiwatch.STUCK_EXIT, r.stderr)
        self.assertIn('did not return from midiInOpen', r.stdout)


if __name__ == '__main__':
    unittest.main()
