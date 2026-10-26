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
            keys.press('BD')                    # a pad's own key: its release would end the hold
            with self.assertRaises(usb._RequestRefused):
                keys.pads([(0, 0xA0, 60)])
            keys.release('BD')
            keys.pads([(0, 0xA0, 60)])
            self.assertEqual(self.fed(), [0xA0, 60, 0xB0, 0])

    def test_script_with_held_keys(self):
        # SCNE + a pad: the script plays while another key is held, and leaves it held
        with usb.RemoteKeys(self.diag) as keys:
            keys.press('SCNE')
            keys.pads([(0, 0xA0, 60)])
            self.assertEqual(self.fed(), [0xA0, 60, 0xB0, 0])
            self.assertTrue(keys.status(0)['owned'])
            keys.release('SCNE')
            self.assertFalse(keys.status(0)['owned'])

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

    # PAD NEXT (revision 2): scripts queued behind the one playing.

    def fed_ms(self):
        """[(ms from the first byte, byte)] of what reached the receiver."""
        at = [self.lib.host_fed_at(i) for i in range(self.lib.host_fed_count())]
        return [((t - at[0]) // pads.TICKS_PER_MS, b) for t, b in zip(at, self.fed())]

    def sim_time(self):
        """The host's clock and sleep on the fixture's clock, so a chain's waits
        pass in the firmware too."""
        lib = self.lib

        def sleep(seconds):
            lib.host_advance(max(1, round(seconds * 1000)))
        return patch.object(usb, 'time', types.SimpleNamespace(
            monotonic=lambda: lib.host_now() / (pads.TICKS_PER_MS * 1000.0), sleep=sleep))

    def run_op(self, keys, builder, events, *after):
        keys.operation += 1
        try:
            return self.diag.call(builder, keys.session, keys.operation, *after, events, timeout=0.25)
        except usb._RequestRefused:
            keys.operation -= 1
            raise

    # Shots while a pad is down (pad step's shots=FILE@MS).

    def test_between_runs_while_the_pad_is_down(self):
        seen = []

        def between(ms):
            seen.append((ms, list(self.fed())))
        with self.sim_time(), usb.RemoteKeys(self.diag) as keys:
            keys.pads(pads.hit('SD', 90, 400, [100, 120], 100, key=None), between=between)
        down = [ms for ms, fed in seen if 0xA1 in fed and 0xB1 not in fed]
        self.assertTrue(down and max(down) > 150, seen[:5])
        self.assertEqual(seen, sorted(seen, key=lambda s: s[0]))   # time only goes forward

    def test_pad_shots_parse(self):
        import analogrun
        self.assertEqual(analogrun.pad_shots('b.png@900,a.png@100', 1000, 1),
                         [(100, 'a.png'), (900, 'b.png')])
        self.assertEqual(analogrun.pad_shots(None, 100, 2), [])
        for text, hold, n in (('a.png@1000', 1000, 1), ('a.png@x', 1000, 1), ('a.png', 1000, 1),
                              ('a.png@10', 1000, 2), ('@10', 1000, 1)):
            with self.assertRaises(ValueError):
                analogrun.pad_shots(text, hold, n)

    def test_shots_during_takes_each_once_in_order(self):
        import analogrun
        taken = []
        session = types.SimpleNamespace(shot=lambda name: (taken.append(name),
                                                           types.SimpleNamespace(w=128, h=64, lit=lambda: 1))[1])
        between = analogrun.Session.shots_during(session, [(100, 'a.png'), (300, 'b.png')])
        with contextlib.redirect_stdout(io.StringIO()):
            for ms in (20, 120, 150, 310, 500):
                between(ms)
        self.assertEqual(taken, ['a.png', 'b.png'])

    def test_caps_queue(self):
        caps = usb.pad_caps(self.diag)
        self.assertEqual((caps['revision'], caps['queue']), (2, 3))

    def test_queue_keeps_the_timing(self):
        # each request costs the fixture 50 ms; the queue keeps the take's times anyway
        plan = [(0, [(0, 0xA0, 60), (200, 0xB0, 0)]), (220, [(0, 0xA1, 61), (5, 0xB1, 0)]),
                (230, [(0, 0xA2, 62), (3, 0xB2, 0)]), (300, [(0, 0xA3, 63), (100, 0xB3, 0)])]
        with self.sim_time(), usb.RemoteKeys(self.diag) as keys:
            self.assertEqual(keys.pad_chain(plan), (0, 0.0))
        self.assertEqual(self.fed_ms(), [(0, 0xA0), (0, 60), (200, 0xB0), (200, 0), (220, 0xA1), (220, 61),
                                         (225, 0xB1), (225, 0), (230, 0xA2), (230, 62), (233, 0xB2),
                                         (233, 0), (300, 0xA3), (300, 63), (400, 0xB3), (400, 0)])

    def test_queue_carries_held_pads_across_the_join(self):
        keys = usb.RemoteKeys(self.diag)
        self.run_op(keys, pads.req_pad_run, [(0, 0x00, 1), (0, 0xA0, 90), (100, 0x90, 40)])
        self.run_op(keys, pads.req_pad_next, [(0, 0x90, 30), (20, 0xB0, 0), (20, 0x00, 0)], 150)
        self.lib.host_advance(30)
        self.assertNotEqual(self.bd_group(), 0)       # past the first script's end: still down
        self.lib.host_advance(200)
        self.assertEqual(self.fed_ms(), [(0, 0xA0), (0, 90), (100, 0x90), (100, 40), (150, 0x90),
                                         (150, 30), (170, 0xB0), (170, 0)])
        self.assertEqual(self.bd_group(), 0)
        self.assertEqual(keys.status()['state'], 'completed')
        keys.cancel()

    def test_queue_full_and_too_far_ahead(self):
        keys = usb.RemoteKeys(self.diag)
        self.run_op(keys, pads.req_pad_run, [(0, 0xA0, 60), (1000, 0xB0, 0)])
        for _ in range(3):
            self.run_op(keys, pads.req_pad_next, [(0, 0xA1, 60), (5, 0xB1, 0)], 100)
        with self.assertRaises(usb._RequestRefused) as e:
            self.run_op(keys, pads.req_pad_next, [(0, 0xA1, 60)], 100)
        self.assertEqual(e.exception.status, 3)        # busy: the ring is full
        keys.cancel()
        keys = usb.RemoteKeys(self.diag)
        self.run_op(keys, pads.req_pad_run, [(0, 0xA0, 60), (2000, 0xB0, 0)])
        self.run_op(keys, pads.req_pad_next, [(0, 0xA1, 60)], 9000)
        with self.assertRaises(usb._RequestRefused) as e:   # 9 + 9 s from now: busy, for now
            self.run_op(keys, pads.req_pad_next, [(0, 0xA1, 60)], 9000)
        self.assertEqual(e.exception.status, 3)
        with self.assertRaises(usb._RequestRefused):         # over RP_MAX_MS: refused
            self.run_op(keys, pads.req_pad_next, [(0, 0xA1, 60)], 10001)
        keys.cancel()

    def test_next_after_the_chain_has_ended(self):
        keys = usb.RemoteKeys(self.diag)
        with self.assertRaises(usb._RequestRefused) as e:     # nothing has played
            self.run_op(keys, pads.req_pad_next, [(0, 0xA1, 60)], 0)
        self.assertEqual(e.exception.status, 4)
        keys.pads([(0, 0xA0, 60)])
        with self.assertRaises(usb._RequestRefused) as e:
            self.run_op(keys, pads.req_pad_next, [(0, 0xA1, 60)], 0)
        self.assertEqual(e.exception.status, 4)
        self.assertEqual(self.fed(), [0xA0, 60, 0xB0, 0])
        keys.cancel()

    def test_chain_restarts_when_it_ran_dry(self):
        # a second script more than the queue reaches ahead (10 s) is started by the host
        plan = [(0, [(0, 0xA0, 60)]), (10500, [(0, 0xA1, 61), (50, 0xB1, 0)]), (10600, [(0, 0xA2, 62)])]
        with self.sim_time(), usb.RemoteKeys(self.diag) as keys:
            dry, late = keys.pad_chain(plan)
        self.assertEqual(dry, 0)
        self.assertEqual([b for _, b in self.fed_ms()], [0xA0, 60, 0xB0, 0, 0xA1, 61, 0xB1, 0, 0xA2, 62, 0xB2, 0])
        ms = dict((b, t) for t, b in self.fed_ms() if b in (0xA1, 0xA2))
        self.assertEqual(ms[0xA2] - ms[0xA1], 100)
        self.assertLess(abs(ms[0xA1] - 10500), 10)              # by the host's clock

    def test_chain_restarts_when_the_queue_ran_dry(self):
        # the first script has played out before its successor arrives (50 ms a request here)
        plan = [(0, [(0, 0xA0, 60)]), (20, [(0, 0xA1, 61)])]
        with self.sim_time(), usb.RemoteKeys(self.diag) as keys:
            dry, late = keys.pad_chain(plan)
        self.assertEqual(dry, 1)
        self.assertGreater(late, 20)
        self.assertEqual(self.fed(), [0xA0, 60, 0xB0, 0, 0xA1, 61, 0xB1, 0])

    def test_cancel_drops_the_queue(self):
        keys = usb.RemoteKeys(self.diag)
        self.run_op(keys, pads.req_pad_run, [(0, 0xA0, 60), (0, 0xC0, 1), (400, 0xB0, 0)])
        self.run_op(keys, pads.req_pad_next, [(0, 0xA1, 61)], 500)
        self.lib.host_advance(20)
        keys.cancel()
        self.lib.host_advance(600)
        self.assertEqual(self.fed(), [0xA0, 60, 0xC0, 1, 0xB0, 0, 0xC0, 0])
        self.assertEqual(self.lib.host_pad_state(), 0)

    def test_replay_carries_a_long_press_whole(self):
        t = pads.TICKS_PER_MS
        entries = [(0, 0xA0, 2), (1, 90, 0)]
        for i in range(1, 121):                                   # pressure every 1.5 ms
            entries += [(i * 3 * t // 2, 0x90, 2), (i * 3 * t // 2 + 1, i, 0)]
        entries += [(200 * t, 0xB0, 2), (200 * t + 1, 0, 0)]
        msgs = pads.messages(entries)
        thinned = pads.replay_plan(msgs)
        self.assertEqual(len(thinned), 1)
        self.assertEqual(len(thinned[0][1]), pads.MAX_EVENTS)
        plan = pads.replay_plan(msgs, carry=True)
        self.assertEqual([len(s) for _, s in plan], [48, 48, 26])
        flat = [(start + ms, h, d) for start, s in plan for ms, h, d in s]
        self.assertEqual(flat, [(e[0] // t, e[1], m) for e, m in
                                zip(entries[::2], [b for _, b, _ in entries[1::2]])])
        with self.sim_time(), usb.RemoteKeys(self.diag) as keys:
            self.assertEqual(keys.pad_chain(plan)[0], 0)
        self.assertEqual([(t_, b) for t_, b in self.fed_ms()][::2],
                         [(ms, h) for ms, h, _ in flat])


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
