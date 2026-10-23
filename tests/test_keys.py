#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""The real C protocol and input bridge (remote_keys.c, core_candidate.c),
compiled for the host behind a fake transport: no MIDI device is opened.
Needs a C compiler (`cc`). The fixture's slot table is synthetic. With
ELEKLOADER_STOCK set to the stock OS 1.74 file and elekloader importable, it
also checks that the unit's own table holds every key code in keys.def."""
import contextlib
import ctypes
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import rytmusb as usb
from keymap import KEYS, MAPS, PRODUCT, SUPPORTED

KEY_TABLE = int(json.load(open(ROOT / 'mod.json'))['defsym']['KEY_TABLE'], 16)


class HostPort:
    def __init__(self, lib):
        self.lib = lib
        self.messages = []
        self.sent = []
        self.closed = False

    def close(self):
        self.closed = True

    def send(self, data):
        data = bytes(data)
        raw = usb.unpack7(data[6:-1])
        self.sent.append(raw)
        reply = ctypes.create_string_buffer(1100)
        count = self.lib.rk_receive(data[6:-1], len(data) - 7, reply)
        if count:
            self.messages.append(usb.HEADER + usb.pack7(reply.raw[:count]) + b'\xf7')

    def receive(self, seconds=1, until=None):
        self.lib.host_advance(50)              # the UI tick, 20 Hz
        messages, self.messages = self.messages, []
        if until:
            until(messages)
        return messages


class Keys(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        library = str(Path(cls.tmp.name) / 'keys.so')
        subprocess.run(['cc', '-std=c99', '-Wall', '-Wextra', '-Werror', '-shared', '-fPIC',
                        str(ROOT / 'tests/fixtures/remote_keys_host.c'), '-o', library], check=True)
        cls.lib = ctypes.CDLL(library)
        cls.lib.rk_receive.argtypes = (ctypes.c_char_p, ctypes.c_uint, ctypes.c_void_p)

    def setUp(self):
        self.lib.host_reset()
        self.port = HostPort(self.lib)
        self.diag = usb.Diag(self.port)

    def group(self, name):
        return self.lib.host_group(self.lib.host_key_group(KEYS[name]))

    def test_capabilities(self):
        keys = usb.RemoteKeys(self.diag)
        self.assertEqual((keys.default_tap, keys.max_tap, keys.max_hold), (100, 250, 2000))
        self.assertEqual(bin(keys.supported).count('1'), 78)
        self.assertEqual(keys.supported, SUPPORTED[PRODUCT])
        keys.cancel()

    def test_hold_and_tap(self):
        with usb.RemoteKeys(self.diag) as keys:
            keys.press('FUNC')
            self.assertEqual(self.group('FUNC'), self.lib.host_key_mask(KEYS['FUNC']))
            keys.tap('PLAY')
            self.assertEqual(self.group('PLAY'), 0)
            self.assertEqual(self.group('FUNC'), self.lib.host_key_mask(KEYS['FUNC']))
            keys.release('FUNC')
        self.assertEqual(self.group('FUNC'), 0)

    def test_every_key_reaches_its_group_and_bit(self):
        with usb.RemoteKeys(self.diag) as keys:
            for name in MAPS[PRODUCT]:
                group = self.lib.host_key_group(KEYS[name])
                mask = self.lib.host_key_mask(KEYS[name])
                self.assertEqual(mask, 1 << (7 * KEYS[name] % 88 % 8), name)
                keys.press(name)
                self.assertEqual(self.lib.host_group(group), mask, name)
                keys.release(name)
                self.assertEqual(self.lib.host_group(group), 0, name)

    def test_cli_sequence(self):
        port = self.port
        with patch.dict(sys.modules, midimido=types.SimpleNamespace(Port=lambda: port)), \
                patch.object(usb.sys, 'platform', 'darwin'), \
                contextlib.redirect_stdout(io.StringIO()):
            usb.main(['keys', 'FUNC(PLAY),GLOBAL,DOWN,YES', '--tap-ms', '50'])
        actions = [struct.unpack('>IIBBH', raw[3:]) for raw in port.sent if raw[2] == usb.ACTION]
        self.assertEqual([(a, key, ms) for _, _, a, key, ms in actions], [
            (usb.PRESS, KEYS['FUNC'], 2000), (usb.TAP, KEYS['PLAY'], 50),
            (usb.RELEASE, KEYS['FUNC'], 0), (usb.TAP, KEYS['GLOBAL'], 50),
            (usb.TAP, KEYS['DOWN'], 50), (usb.TAP, KEYS['YES'], 50)])
        self.assertTrue(port.closed)

    def test_physical_key_is_kept_and_overlap_refused(self):
        func_group = self.lib.host_key_group(KEYS['FUNC'])
        func_mask = self.lib.host_key_mask(KEYS['FUNC'])
        self.lib.host_physical_group(func_group, func_mask)
        with usb.RemoteKeys(self.diag) as keys:
            with self.assertRaisesRegex(RuntimeError, 'overlap'):
                keys.press('FUNC')
            keys.tap('TRK')
        self.assertEqual(self.lib.host_group(func_group), func_mask)

    def test_busy_queue_defers_then_completes(self):
        keys = usb.RemoteKeys(self.diag)
        self.lib.host_busy(1)
        keys.operation += 1
        report = keys._report(self.diag.call(usb.req_action, keys.session, keys.operation,
                                             usb.TAP, KEYS['YES'], 100), keys.operation)
        self.lib.host_advance(50)
        self.assertEqual(keys.status()['state'], 'accepted')
        self.assertEqual(self.group('YES'), 0)
        self.lib.host_busy(0)
        self.assertEqual(keys._wait(report)['state'], 'completed')
        keys.cancel()

    def test_remote_keys_off_refuses_and_releases(self):
        keys = usb.RemoteKeys(self.diag)
        keys.press('FUNC')
        self.lib.host_off(1)
        self.lib.host_advance(50)
        self.assertEqual(self.group('FUNC'), 0)
        with self.assertRaisesRegex(ValueError, 'REMOTE KEYS'):
            keys.tap('PLAY')
        self.lib.host_off(0)
        self.assertEqual(keys.status(0)['state'], 'cancelled')

    def test_hold_lease_expires(self):
        keys = usb.RemoteKeys(self.diag)
        keys.press('FUNC', 1000)
        self.lib.host_advance(800)
        self.assertEqual(self.group('FUNC'), self.lib.host_key_mask(KEYS['FUNC']))
        self.lib.host_advance(300)
        self.assertEqual(self.group('FUNC'), 0)
        self.assertEqual(keys.status(0)['state'], 'expired')

    def test_unknown_names_refused_before_any_action(self):
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                usb.key_args(['FUNC(SRC2)'])
        self.assertEqual(self.port.sent, [])

    def test_key_missing_from_the_units_table_is_not_offered(self):
        self.lib.host_unmap(KEYS['FX'])
        with self.assertRaisesRegex(ValueError, 'unsupported key helper'):
            usb.RemoteKeys(self.diag)

    def test_turns_reach_the_encoder_input(self):
        with usb.RemoteKeys(self.diag) as keys:
            keys.turn('E', 5, step=2)                     # 2 + 2 + 1, one a tick
            self.assertEqual(self.lib.host_turned(4), 5)
            self.assertEqual(self.lib.host_turn_calls(), 3)
            keys.turn('LEVEL', -64)                       # 30 + 30 + 4 ticks of -1
            self.assertEqual(self.lib.host_turned(8), -64)
            self.assertEqual(self.lib.host_turn_calls(), 67)

    def test_turn_with_a_held_key(self):
        port = self.port
        with patch.dict(sys.modules, midimido=types.SimpleNamespace(Port=lambda: port)),                 patch.object(usb.sys, 'platform', 'darwin'),                 contextlib.redirect_stdout(io.StringIO()):
            usb.main(['turn', 'a', '-6', '--step', '3', '--hold', 'FUNC'])
        actions = [struct.unpack('>IIBBH', raw[3:]) for raw in port.sent if raw[2] == usb.ACTION]
        self.assertEqual([(a, key) for _, _, a, key, _ in actions],
                         [(usb.PRESS, KEYS['FUNC']), (usb.TURN, 1), (usb.RELEASE, KEYS['FUNC'])])
        self.assertEqual(actions[1][4], (0xFD << 8) | 2)
        self.assertEqual(self.lib.host_turned(0), -6)
        self.assertEqual(self.group('FUNC'), 0)

    def test_busy_queue_defers_a_turn(self):
        keys = usb.RemoteKeys(self.diag)
        self.lib.host_busy(1)
        keys.operation += 1
        report = keys._report(self.diag.call(usb.req_action, keys.session, keys.operation,
                                             usb.TURN, 2, (1 << 8) | 3), keys.operation)
        self.lib.host_advance(50)
        self.assertEqual(self.lib.host_turned(1), 0)
        self.lib.host_busy(0)
        self.assertEqual(keys._wait(report)['state'], 'completed')
        self.assertEqual(self.lib.host_turned(1), 3)
        keys.cancel()

    def test_bad_turns_refused(self):
        keys = usb.RemoteKeys(self.diag)
        for encoder, duration in ((0, (1 << 8) | 1), (10, (1 << 8) | 1), (1, (1 << 8) | 0),
                                  (1, (1 << 8) | 31), (1, 0x0001), (1, (31 << 8) | 1),
                                  (1, (0xE1 << 8) | 1)):
            with self.assertRaisesRegex(ValueError, 'refused'):
                self.diag.call(usb.req_action, keys.session, keys.operation + 1, usb.TURN,
                               encoder, duration, timeout=0.25)
        self.assertEqual(self.lib.host_turn_calls(), 0)
        keys.cancel()

    def test_selftest(self):
        with contextlib.redirect_stdout(io.StringIO()):
            usb.selftest()


class StockTable(unittest.TestCase):
    """Every key code in keys.def is in OS 1.74's own slot table, once, as the
    firmware's run-time lookup needs. Reads the table from your stock file."""

    def test_every_code_is_in_the_units_table(self):
        stock = os.environ.get('ELEKLOADER_STOCK')
        if not stock:
            self.skipTest('ELEKLOADER_STOCK (Analog-Rytm_MKII_OS1.74.syx) not set')
        try:
            from elekloader import devices, syx
        except ImportError:
            self.skipTest('elekloader is not importable')
        s = syx.Syx.load(stock)
        dev, _rel = devices.identify(s.sha256)
        image, base = s.section(dev.main_section), dev.main_load

        def u32(a):
            return struct.unpack_from('>I', image, a - base)[0]
        table = u32(KEY_TABLE)
        slots = [u32(table + 4 * i) for i in range(88)]
        for name, code in MAPS[PRODUCT].items():
            self.assertEqual(slots.count(code), 1, name)


if __name__ == '__main__':
    unittest.main()
