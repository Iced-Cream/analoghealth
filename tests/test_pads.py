#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""The pad player and the panel log (remote_pads.c) behind the same host
fixture as test_keys: the panel receiver is a recorder of the bytes fed to it."""
import contextlib
import io
import os
import sys
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_keys as tk
from test_keys import HostPort, usb
import analogpads as pads
from keymap import RYTM


class Pads(unittest.TestCase):
    setUpClass = classmethod(tk.Keys.setUpClass.__func__)
    PRODUCT = RYTM

    def setUp(self):
        self.lib.host_reset()
        self.port = HostPort(self.lib)
        self.diag = usb.Diag(self.port)

    def fed(self):
        return [self.lib.host_fed(i) for i in range(self.lib.host_fed_count())]

    def bd_group(self):
        return self.lib.host_group(self.lib.host_key_group(usb.KEYSETS[RYTM]['BD']))

    def test_caps(self):
        self.lib.host_threshold(23)
        caps = usb.pad_caps(self.diag)
        self.assertEqual((caps['pads'], caps['events'], caps['max_ms'], caps['threshold']),
                         (12, 48, 10000, 23))

    def test_strike_with_pressure_and_bridge_key(self):
        with usb.RemoteKeys(self.diag) as keys:
            keys.pads(pads.hit('SD', 77, 100, [10, 20, 30], 20))
        self.assertEqual(self.fed(), [0xA1, 77, 0x91, 10, 0x91, 20, 0x91, 30, 0xB1, 0])
        self.assertEqual(self.lib.host_group(self.lib.host_key_group(usb.KEYSETS[RYTM]['SD'])), 0)
        self.assertGreaterEqual(self.lib.host_calls(), 2)        # key down, key up

    def test_key_as_panel_message(self):
        with usb.RemoteKeys(self.diag) as keys:
            keys.pads(pads.hit('CB', 200, 30, key='panel'))
        self.assertEqual(self.fed(), [0xCB, 1, 0xAB, 200, 0xBB, 0, 0xCB, 0])

    def test_waits_for_the_panel(self):
        keys = usb.RemoteKeys(self.diag)
        self.lib.host_in_panel(1)
        keys.operation += 1
        self.diag.call(pads.req_pad_run, keys.session, keys.operation, [(0, 0xA0, 90), (5, 0xB0, 0)],
                       timeout=0.25)
        self.lib.host_advance(20)
        self.assertEqual(self.fed(), [])
        self.lib.host_in_panel(0)
        self.lib.host_idle(0)                           # in the middle of a panel message
        self.lib.host_advance(20)
        self.assertEqual(self.fed(), [])
        self.lib.host_idle(1)
        self.lib.host_advance(20)
        self.assertEqual(self.fed(), [0xA0, 90, 0xB0, 0])
        keys.cancel()

    def test_cancel_lets_go_of_a_held_pad(self):
        keys = usb.RemoteKeys(self.diag)
        keys.operation += 1
        self.diag.call(pads.req_pad_run, keys.session, keys.operation,
                       [(0, 0x00, 1), (0, 0xA0, 90), (0, 0xC0, 1), (9000, 0xB0, 0)], timeout=0.25)
        self.lib.host_advance(30)
        self.assertEqual(self.fed(), [0xA0, 90, 0xC0, 1])
        self.assertNotEqual(self.bd_group(), 0)
        keys.cancel()
        self.assertEqual(self.fed(), [0xA0, 90, 0xC0, 1, 0xB0, 0, 0xC0, 0])
        self.assertEqual(self.bd_group(), 0)
        self.assertEqual(self.lib.host_pad_state(), 0)

    def test_script_lets_go_at_its_end(self):
        with usb.RemoteKeys(self.diag) as keys:
            keys.pads([(0, 0xA5, 60), (10, 0x95, 40)])
        self.assertEqual(self.fed(), [0xA5, 60, 0x95, 40, 0xB5, 0])

    def test_refused(self):
        with usb.RemoteKeys(self.diag) as keys:
            for bad in ([(0, 0xAC, 1)], [(0, 0x20, 1)], [(5, 0xA0, 1), (4, 0xB0, 0)],
                        [(10001, 0xA0, 1)]):
                keys.operation += 1
                with self.assertRaises(usb._RequestRefused):
                    self.diag.call(pads.req_pad_run, keys.session, keys.operation, bad, timeout=0.25)
                keys.operation -= 1
            keys.press('FUNC')
            with self.assertRaises(usb._RequestRefused):
                keys.pads([(0, 0xA0, 60)])
            keys.release('FUNC')
            keys.pads([(0, 0xA0, 60)])
            self.assertEqual(self.fed(), [0xA0, 60, 0xB0, 0])

    def test_keys_wait_while_a_script_runs(self):
        keys = usb.RemoteKeys(self.diag)
        keys.operation += 1
        self.diag.call(pads.req_pad_run, keys.session, keys.operation, [(0, 0xA0, 60), (500, 0xB0, 0)],
                       timeout=0.25)
        keys.operation += 1
        with self.assertRaises(usb._RequestRefused):
            self.diag.call(usb.req_action, keys.session, keys.operation, usb.TAP,
                           usb.KEYSETS[RYTM]['PLAY'], 50, timeout=0.25)
        keys.operation -= 1
        keys.cancel()

    def test_remote_keys_off(self):
        keys = usb.RemoteKeys(self.diag)
        self.lib.host_off(1)
        with self.assertRaises(usb._RequestRefused):
            keys.pads([(0, 0xA0, 60)])
        self.lib.host_off(0)

    def test_capture(self):
        self.lib.host_threshold(10)
        self.diag.call(pads.req_capture, 1)
        for byte, header in ((0x91, 1), (33, 0), (0x20, 1), (0x04, 0)):
            self.lib.host_panel(byte, header)
        with usb.RemoteKeys(self.diag) as keys:
            keys.pads([(0, 0xA2, 80)])
        logged, first, entries = pads.decode_read(self.diag.call(pads.req_capture_read, 0))
        self.assertEqual((logged, first), (8, 0))
        self.assertEqual([(b, f) for _, b, f in entries],
                         [(0x91, 2), (33, 0), (0x20, 2), (0x04, 0), (0xA2, 3), (80, 1), (0xB2, 3), (0, 1)])
        msgs = pads.messages(entries)
        self.assertEqual([m['bytes'] for m in msgs], [[0x91, 33], [0x20, 4], [0xA2, 80], [0xB2, 0]])
        self.assertEqual([m['ours'] for m in msgs], [False, False, True, True])
        self.assertIn('SD pressure 33', pads.describe(msgs[0]))
        self.assertIn('group 0', pads.describe(msgs[1]))
        self.assertIn('(pads BD,CP)', pads.describe({'bytes': [0x29, 0x09]}))
        self.diag.call(pads.req_capture, 0)
        self.lib.host_panel(0x91, 1)
        self.assertEqual(pads.decode_read(self.diag.call(pads.req_capture_read, 0))[0], 8)

    def test_capture_wraps(self):
        self.diag.call(pads.req_capture, 1)
        for i in range(600):
            self.lib.host_panel(i & 0x7f, 0)
        logged, first, entries = pads.decode_read(self.diag.call(pads.req_capture_read, 0))
        self.assertEqual((logged, first, len(entries)), (600, 88, 40))
        self.assertEqual(entries[0][1], 88 & 0x7f)

    def test_cli_pad_and_replay(self):
        self.lib.host_threshold(0)
        port = self.port
        out = io.StringIO()
        with patch.dict(sys.modules, midimido=types.SimpleNamespace(Port=lambda *_: port)), \
                patch.object(usb.sys, 'platform', 'darwin'), contextlib.redirect_stdout(out):
            usb.main(['pad', 'BD,CP', '--vel', '127', '--hold', '40'])
        raw = pads.raw_for(127, 0)
        self.assertEqual(self.fed(), [0xA0, raw, 0xB0, 0, 0xA3, raw, 0xB3, 0])
        self.assertIn('BD struck: raw %d, velocity 127' % raw, out.getvalue())

    def test_replay_from_a_log(self):
        t = pads.TICKS_PER_MS
        entries = [(1000, 0x29, 2), (1000 + 1, 0x01, 0),            # group 9 bit 0: BD key down
                   (1000 + 2 * t, 0xA0, 2), (1000 + 2 * t + 1, 70, 0),
                   (1000 + 9 * t, 0x90, 2), (1000 + 9 * t + 1, 50, 0),
                   (1000 + 50 * t, 0xB0, 2), (1000 + 50 * t + 1, 0, 0),
                   (1000 + 51 * t, 0x29, 2), (1000 + 51 * t + 1, 0x00, 0),
                   (1000 + 52 * t, 0x31, 2), (1000 + 52 * t + 1, 0x02, 0)]   # an encoder: not replayed
        scripts = pads.replay_script(pads.messages(entries))
        self.assertEqual(scripts, [[(0, 0x00, 1), (2, 0xA0, 70), (9, 0x90, 50), (50, 0xB0, 0),
                                    (51, 0x00, 0)]])
        with usb.RemoteKeys(self.diag) as keys:
            for s in scripts:
                keys.pads(s)
        self.assertEqual(self.fed(), [0xA0, 70, 0x90, 50, 0xB0, 0])


class Curve(unittest.TestCase):
    def test_velocity(self):
        self.assertEqual(pads.velocity(0, 0), 0)
        self.assertEqual(pads.velocity(255, 0), 127)
        for t in (0, 12, 40):
            for v in (1, 64, 100, 127):
                r = pads.raw_for(v, t)
                self.assertGreaterEqual(pads.velocity(r, t), v)
                self.assertTrue(r == 0 or pads.velocity(r - 1, t) < v)


if __name__ == '__main__':
    unittest.main()
