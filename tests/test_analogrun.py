#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""analogrun.py and elkrpc.py against a fake unit that answers Elektron's RPC
(Ping, SoftwareVersion, Screenshot, the data store) and sends a dump when
asked: no MIDI device is opened, and no key session (that is test_keys.py)."""
from pathlib import Path
import json
import os
import struct
import sys
import tempfile
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import elkrpc
import analogrun
import testrules
from analogusb import pack7, unpack7

DUMP_REQUEST = analogrun.dump_request(analogrun.KIT_REQUEST, 0)


def make_kit(name, number=0):
    """A kit dump: its body holds the name at bytes 4-19."""
    body = bytes(4) + name.encode().ljust(16, b'\0') + bytes(range(40))
    return (bytes([0xF0, 0x00, 0x20, 0x3C, 0x07, 0x00, 0x52, 0x01, 0x01, number]) + pack7(body)
            + bytes([0x00, 0x00, 0x00, 0x05, 0xF7]))


KIT = [make_kit('~T KIT 1')]


class FakeUnit:
    """Replies to each request when the port is next read."""

    def __init__(self, block=0x2000):
        self.pending, self.short, self.seq = [], [], 0x4000
        self.store = {'/projects/1': bytes(range(256)) * 50}
        self.names = {'/projects/1': 'MY SONG'}
        self.jobs = {}
        self.block = block
        self.mangle = False
        w, h = 128, 64
        cols = [(1 << 63) | (x & 1) for x in range(w)]     # row 0 = LSB, row 63 = top bit
        self.pixels = b''.join(c.to_bytes(8, 'big') for c in cols)

    # --- the port interface ---------------------------------------------------
    def send(self, data):
        data = bytes(data)
        if data == DUMP_REQUEST:
            self.pending += [bytes(m) for m in getattr(self, 'kit', KIT)]
            return
        r = elkrpc.parse(data)
        if r is None:
            if data[:7] == KIT[0][:7]:              # a kit dump: stored at once
                kit = getattr(self, 'incoming', []) + [data]
                self.incoming = kit if len(kit) < len(KIT) else []
                if len(kit) == len(KIT):
                    self.kit = [kit[0][:-2] + b'\x7F\xF7'] + kit[1:] if self.mangle else kit
                    self.received_kits = getattr(self, 'received_kits', 0) + 1
            return
        msg = unpack7(data[6:-1])
        seq, _, cmd = struct.unpack_from('>HHB', msg)
        args = self.answer(cmd, msg[5:])
        self.seq += 1
        self.pending.append(elkrpc.HEADER + pack7(struct.pack('>HHB', self.seq, seq, cmd | 0x80)
                                                  + args) + b'\xF7')

    def send_short(self, *data):
        self.short.append(data)

    def receive(self, seconds=1.0, until=None):
        msgs, self.pending = self.pending, []
        if until:
            until(msgs)
        return msgs

    def close(self):
        pass

    # --- the device -----------------------------------------------------------
    def answer(self, cmd, a):
        if cmd == elkrpc.PING:
            return bytes([0x10, 2, 0x01, 0x04]) + b'Analog Rytm MKII\0'
        if cmd == elkrpc.VERSION:
            return b'0174\x001.74\x00'
        if cmd == elkrpc.SCREENSHOT:
            return struct.pack('>BHHI', 1, 128, 64, len(self.pixels)) + self.pixels
        if cmd == elkrpc.DATA_LIST:
            path = a[:a.index(0)].decode()
            out = b'\x01' + bytes(12)
            for name, data in sorted(self.store.items()):
                if name.startswith(path + '/'):
                    out += (self.names.get(name, 'NEW').encode() + b'\0' + bytes([0, 2])
                            + struct.pack('>iIHBB', int(name.rsplit('/', 1)[1]), len(data), 7, 1, 0))
            return out
        if cmd == elkrpc.DATA_READ_OPEN:
            path = a[:a.index(0)].decode()
            if path not in self.store:
                return b'\x00no such object\0'
            job = len(self.jobs) + 1
            self.jobs[job] = ('r', path)
            return b'\x01' + struct.pack('>IIB', job, self.block, 1)
        if cmd == elkrpc.DATA_READ_PARTIAL:
            job, seq = struct.unpack('>II', a)
            data = self.store[self.jobs[job][1]]
            part = data[seq * self.block:(seq + 1) * self.block]
            last = (seq + 1) * self.block >= len(data)
            return b'\x01' + struct.pack('>IIIBII', job, seq, 1000 * min(len(data), (seq + 1) * self.block)
                                         // len(data), last, 0, len(part)) + part
        if cmd in (elkrpc.DATA_READ_CLOSE,):
            job, = struct.unpack('>I', a)
            return b'\x01' + struct.pack('>II', job, 0)
        if cmd == elkrpc.DATA_WRITE_OPEN:
            size, = struct.unpack_from('>I', a)
            path = a[4:a.index(0, 4)].decode()
            job = len(self.jobs) + 1
            self.jobs[job] = ('w', path, bytearray(), size)
            return b'\x01' + struct.pack('>I', job)
        if cmd == elkrpc.DATA_WRITE_PARTIAL:
            job, seq, crc, n = struct.unpack_from('>IIII', a)
            block = a[16:16 + n]
            if crc != zlib.crc32(block, 0xFFFFFFFF) & 0xFFFFFFFF:
                return b'\x00bad crc\0'
            buf = self.jobs[job][2]
            buf += block
            return b'\x01' + struct.pack('>III', job, seq, len(buf))
        if cmd == testrules.DATA_RENAME:
            path, name = a.split(b'\0')[:2]
            if path.decode() not in self.store:
                return bytes([0]) + b'Rename: Not a saved project\0'
            self.names[path.decode()] = name.decode()
            return bytes([1])
        if cmd == elkrpc.DATA_CLEAR:
            path = a[:a.index(0)].decode()
            self.names.pop(path, None)
            if self.store.pop(path, None) is None:
                return b'\0empty\0'
            return bytes([1])
        if cmd == elkrpc.DATA_WRITE_CLOSE:
            job, size = struct.unpack('>II', a)
            _, path, buf, _ = self.jobs[job]
            self.store[path] = bytes(buf[:-1] + b'\xEE') if self.mangle else bytes(buf)
            return b'\x01' + struct.pack('>II', job, len(buf))
        return b''


class RpcTest(unittest.TestCase):
    def setUp(self):
        self.unit = FakeUnit()
        self.rpc = elkrpc.Rpc(self.unit)

    def test_identity(self):
        self.assertEqual(self.rpc.ping(), (0x10, 'Analog Rytm MKII'))
        self.assertEqual(self.rpc.version(), '1.74')

    def test_screenshot_orientation_and_png(self):
        s = self.rpc.screenshot()
        rows = s.rows()
        self.assertEqual((s.w, s.h), (128, 64))
        self.assertEqual(rows[0][:4], [0, 1, 0, 1])          # LSB of each column is row 0
        self.assertEqual(rows[63], [1] * 128)                # the top bit is the last row
        self.assertEqual(s.lit(), 128 + 64)
        png = s.png(2)
        self.assertTrue(png.startswith(b'\x89PNG\r\n\x1a\n'))
        self.assertEqual(struct.unpack('>II', png[16:24]), (256, 128))

    def test_data_store_read_write(self):
        self.assertEqual([e['id'] for e in self.rpc.data_list('/projects')], [1])
        data = self.rpc.data_read('/projects/1')
        self.assertEqual(data, self.unit.store['/projects/1'])
        self.rpc.data_write('/projects/2', data[::-1])
        self.assertEqual(self.unit.store['/projects/2'], data[::-1])
        with self.assertRaises(elkrpc.RpcError):
            self.rpc.data_read('/projects/9')

    def test_ignores_other_traffic(self):
        self.unit.pending.append(KIT[0])
        self.assertEqual(self.rpc.version(), '1.74')


class SessionTest(unittest.TestCase):
    """As on a shared test rig: the rules are on."""

    def setUp(self):
        saved, testrules.enforced = testrules.enforced, lambda: True
        self.addCleanup(setattr, testrules, 'enforced', saved)
        self.unit = FakeUnit()
        self.tmp = tempfile.TemporaryDirectory()
        self.s = analogrun.Session(self.unit, self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_steps_without_keys(self):
        self.s.run('cc 10 74 64; nrpn 1 1 2 300\nnote 2 36 100 1  # kick\nshot a.png; shot a.bin\n'
                   'expect-screen a.bin; say ok')
        self.assertEqual(self.unit.short[:5], [(0xB9, 74, 64), (0xB0, 99, 1), (0xB0, 98, 2),
                                               (0xB0, 6, 2), (0xB0, 38, 44)])
        self.assertEqual(self.unit.short[5:], [(0x91, 36, 100), (0x81, 36, 0)])
        self.assertTrue(Path(self.tmp.name, 'a.png').read_bytes().startswith(b'\x89PNG'))
        self.unit.pixels = bytes(len(self.unit.pixels))
        with self.assertRaises(AssertionError):
            self.s.step('expect-screen a.bin')
        self.assertTrue(Path(self.tmp.name, 'a-got.png').exists())

    def test_screenshot_inside_a_hold(self):
        events = []
        unit = self.unit

        class Keys:
            def press(self, key, ms=None):
                events.append(('press', key, ms))

            def release(self, key):
                events.append(('release', key))

            def tap(self, key, ms=None):
                events.append(('tap', key))

            def cancel(self):
                events.append(('cancel',))
        send = unit.send
        unit.send = lambda d: (events.append(('rpc',)) if elkrpc.parse(d) else None, send(d))
        self.s._keys = Keys()
        self.s.run('hold SRC 1500; shot held.png; release SRC; keys FUNC(PLAY)')
        self.s.close()
        self.assertEqual(events, [('press', 'SRC', 1500), ('rpc',), ('release', 'SRC'),
                                  ('press', 'FUNC', None), ('tap', 'PLAY'), ('release', 'FUNC'),
                                  ('cancel',)])

    def test_unknown_step(self):
        with self.assertRaises(ValueError):
            self.s.step('jump 3')

    def test_dump_capture_keeps_messages_seen_during_other_requests(self):
        msgs = self.s.capture(request=DUMP_REQUEST, quiet=0.05)
        self.assertEqual(msgs, KIT)

    def test_dump_request_layout(self):
        self.assertEqual(analogrun.dump_request(0x62, 5).hex(' '),
                         'f0 00 20 3c 07 00 62 01 01 05 00 00 00 05 f7')
        with self.assertRaises(ValueError):
            analogrun.dump_request(0x62, 128)

    def test_dump_device_from_ping(self):
        # The fake Rytm answers product 0x10: device byte 07.
        self.assertEqual(self.s.dump_request(0x62, 5)[4], 0x07)
        # An A4 MKII (0x0E) gets 06, whatever its name; other units have no dumps here.
        s = analogrun.Session(self.unit, self.tmp.name)
        s.rpc.ping = lambda: (0x0E, 'Analog Keys')
        self.assertEqual(s.dump_request(0x68).hex(' '),
                         'f0 00 20 3c 06 00 68 01 01 00 00 00 00 05 f7')
        s = analogrun.Session(self.unit, self.tmp.name)
        s.rpc.ping = lambda: (0x14, 'Digitone')
        with self.assertRaises(ValueError):
            s.dump_request(0x62)
        a4_kit = make_kit('~T KIT 1')
        a4_kit = a4_kit[:4] + b'\x06' + a4_kit[5:]
        self.assertEqual(testrules.kit_name(a4_kit), '~T KIT 1')
        self.assertTrue(testrules.is_kit_dump(a4_kit))

    def test_kit_roundtrip_command(self):
        unit = self.unit

        class Port(FakeUnit):
            def __new__(cls, *_name):
                return unit
        saved, analogrun.open_port = analogrun.open_port, Port
        self.addCleanup(setattr, analogrun, 'open_port', saved)
        out = Path(self.tmp.name, 'kit')
        self.assertEqual(analogrun.main(['kit-roundtrip', str(out), '--kit', '0', '--settle', '0']), 0)
        self.assertEqual(analogrun.load_syx(out / 'kit-back.syx'), KIT)
        unit.mangle = True
        self.assertEqual(analogrun.main(['kit-roundtrip', str(out), '--kit', '0', '--settle', '0']), 1)
        with self.assertRaises(SystemExit):
            analogrun.main(['kit-roundtrip', str(out)])
        # The person's project is active (its kit 1 is not ~T): no kit goes to the unit.
        unit.kit, unit.mangle, unit.received_kits = [make_kit('KIT A')], False, 0
        self.assertEqual(analogrun.main(['kit-roundtrip', str(out), '--kit', '0', '--settle', '0']), 2)
        self.assertEqual(unit.received_kits, 0)

    def test_active_command(self):
        import contextlib, io
        unit = self.unit

        class Port(FakeUnit):
            def __new__(cls, *_name):
                return unit
        saved, analogrun.open_port = analogrun.open_port, Port
        self.addCleanup(setattr, analogrun, 'open_port', saved)
        for name, verdict in (('~T KIT 1', 'a test project'), ('KIT A', 'not a test project')):
            unit.kit = [make_kit(name)]
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(analogrun.main(['active']), 0)
            self.assertIn('%r (%s)' % (name, verdict), out.getvalue())

    def test_send_step_checks_the_active_project(self):
        path = Path(self.tmp.name, 'k.syx')
        analogrun.save_syx(path, [make_kit('~T KIT 1', 5)])
        self.unit.kit = [make_kit('KIT A')]
        with self.assertRaises(testrules.RuleError):
            self.s.step('send k.syx')
        self.assertEqual(getattr(self.unit, 'received_kits', 0), 0)
        self.unit.kit = [make_kit('~T KIT 1')]
        self.s.step('send k.syx')
        self.assertEqual(self.unit.received_kits, 1)

    def test_syx_files_and_compare(self):
        two = [KIT[0], make_kit('X', 1)]
        path = Path(self.tmp.name, 'k.syx')
        analogrun.save_syx(path, two)
        self.assertEqual(analogrun.load_syx(path), two)
        self.assertEqual(analogrun.compare(two, two), [])
        self.assertEqual(analogrun.compare(two, [two[0], two[1][:-2] + b'\x06\xF7']),
                         [(1, len(two[1]) - 2)])

    def test_data_roundtrip_command(self):
        unit = self.unit

        class Port(FakeUnit):
            def __new__(cls, *_name):
                return unit
        saved, analogrun.open_port = analogrun.open_port, Port
        self.addCleanup(setattr, analogrun, 'open_port', saved)
        out = Path(self.tmp.name, 'rt')
        run = lambda *a: analogrun.main(['data', *a])
        # Empty slots outside 121-128, and empty slots without a ~T name: refused.
        self.assertEqual(run('roundtrip', '/projects/1', str(out), '--to', '/projects/5', '--yes'), 2)
        self.assertEqual(run('roundtrip', '/projects/1', str(out), '--to', '/projects/121', '--yes'), 2)
        self.assertNotIn('/projects/121', unit.store)
        # Into an empty test slot, named ~T in the same command.
        self.assertEqual(run('roundtrip', '/projects/1', str(out), '--to', '/projects/121',
                             '--name', '~t copy', '--yes'), 0)
        self.assertEqual(unit.store['/projects/121'], unit.store['/projects/1'])
        self.assertEqual(unit.names['/projects/121'], '~T COPY')
        # A ~T item keeps its name; the person's project is never a target.
        self.assertEqual(run('roundtrip', '/projects/1', str(out), '--to', '/projects/121', '--yes'), 0)
        self.assertEqual(unit.names['/projects/121'], '~T COPY')
        self.assertEqual(run('roundtrip', '/projects/1', str(out), '--yes'), 2)
        self.assertEqual(run('roundtrip', '/projects/121', str(out), '--to', '/projects/1', '--yes'), 2)
        self.assertEqual(run('roundtrip', '/projects/1', str(out), '--to', '/projects/122',
                             '--name', 'COPY', '--yes'), 2)
        unit.mangle = True
        self.assertEqual(run('roundtrip', '/projects/1', str(out), '--to', '/projects/122',
                             '--name', '~T BAD', '--yes'), 1)
        unit.mangle = False
        with self.assertRaises(SystemExit):
            run('put', '/projects/121', 'x.bin')
        src = Path(self.tmp.name, 'p.bin')
        src.write_bytes(bytes(100))
        self.assertEqual(run('put', '/projects/1', str(src), '--yes'), 2)
        self.assertEqual(unit.store['/projects/1'], bytes(range(256)) * 50)
        self.assertEqual(run('put', '/projects/121', str(src), '--yes'), 0)
        self.assertEqual((unit.store['/projects/121'], unit.names['/projects/121']), (bytes(100), '~T COPY'))
        backup = Path(self.tmp.name, 'p121.bin')
        self.assertEqual(run('clear', '/projects/1', str(backup), '--yes'), 2)
        self.assertIn('/projects/1', unit.store)
        self.assertEqual(run('clear', '/projects/121', str(backup), '--yes'), 0)
        self.assertNotIn('/projects/121', unit.store)
        self.assertEqual(backup.read_bytes(), bytes(100))
        with self.assertRaises(SystemExit):
            run('clear', '/projects/1', str(backup))


class PlanTest(unittest.TestCase):
    """analogrun run PLAN against the fake unit: one verdict, a run folder."""

    def setUp(self):
        import testplan
        self.testplan = testplan
        self.unit = unit = FakeUnit()

        class Port(FakeUnit):
            def __new__(cls, *_name):
                return unit
        saved, analogrun.open_port = analogrun.open_port, Port
        self.addCleanup(setattr, analogrun, 'open_port', saved)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def plan(self, text):
        path = Path(self.tmp.name, 'p.txt')
        path.write_text(text)
        return str(path)

    def run_plan(self, text, quiet=True):
        import contextlib, io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = analogrun.main((['--quiet'] if quiet else []) + ['run', self.plan(text)])
        return rc, out.getvalue()

    def test_record_then_compare(self):
        head = 'name = home check\nemulator = yes\nrefs = refs\n'
        rc, out = self.run_plan(head + 'record-refs = yes\n[boot]\nexpect-screen home\n[midi]\ncc 10 74 64\n')
        self.assertEqual(rc, 0, out)
        self.assertTrue(Path(self.tmp.name, 'refs', 'home.bin').exists())
        rc, out = self.run_plan(head + '[boot]\nexpect-screen home\n[midi]\ncc 10 74 64\n')
        self.assertEqual((rc, out.count('\n')), (0, 1), out)      # quiet: one line on success
        self.assertIn('home check on the emulator: PASS (2 steps', out)
        self.unit.pixels = bytes(len(self.unit.pixels))
        rc, out = self.run_plan(head + '[boot]\nexpect-screen home\n[midi]\ncc 10 74 64\n')
        self.assertEqual(rc, 1)
        self.assertIn('FAIL boot', out)
        self.assertIn('skip midi', out)
        self.assertIn('first failure, boot: AssertionError: screen differs from home.bin: 192 pixels '
                      'differ in rows 0-63, columns 0-127', out)
        runs = sorted(Path(self.tmp.name, 'runs').iterdir())
        self.assertTrue(any(f.name == 'home-got.png' for f in runs[-1].iterdir()))
        summary = json.loads((runs[-1] / 'summary.json').read_text())
        self.assertEqual((summary['pass'], summary['steps'][1]['result']), (False, 'skip'))

    def test_hardware_flash_needs_an_emulator_run(self):
        image = Path(self.tmp.name, 'os.syx')
        image.write_bytes(b'\xF0\xF7')
        rc, out = self.run_plan('unit = rytm\nflash = os.syx\n[boot]\nwait 0\n')
        self.assertEqual(rc, 1)
        self.assertIn('first failure, flash: a hardware flash needs emulator-run', out)
        emu = Path(self.tmp.name, 'runs', 'emu-ok')
        emu.mkdir(parents=True)
        (emu / 'summary.json').write_text(json.dumps({'emulator': True, 'pass': True,
                                                     'flash_sha256': 'other'}))
        rc, out = self.run_plan('unit = rytm\nflash = os.syx\nemulator-run = runs/emu-ok\n[boot]\nwait 0\n')
        self.assertIn('flashed a different OS file', out)

    def test_plain_steps_file_is_not_a_plan(self):
        self.assertFalse(self.testplan.is_plan(self.plan('cc 1 2 3\nwait 0\n')))
        self.assertTrue(self.testplan.is_plan(self.plan('[a]\nwait 0\n')))

    def test_screen_diff_text(self):
        w, h = 128, 64
        blank = bytes(w * 8)
        cols = [0] * w
        cols[5] = 1 << 3                       # row 3 lit in column 5
        lit = b''.join(c.to_bytes(8, 'big') for c in cols)
        diff, text = analogrun.screen_diff(lit, blank, w, h)
        self.assertEqual(diff, [(3, 5)])
        self.assertTrue(text.startswith('1 pixels differ in rows 3-3, columns 5-5'))
        self.assertIn('\n    +', text)
        # rows=: only those rows are compared and reported
        self.assertEqual(analogrun.screen_diff(lit, blank, w, h, (4, 63)), ([], 'identical'))
        diff, text = analogrun.screen_diff(lit, blank, w, h, (0, 5))
        self.assertEqual(diff, [(3, 5)])
        self.assertIn('(comparing rows 0-5)', text)

    def test_expect_screen_rows_option(self):
        w = 128
        cols = [0] * w
        cols[5] = 1 << 3
        lit = b''.join(c.to_bytes(8, 'big') for c in cols)
        d = tempfile.mkdtemp()
        with open(os.path.join(d, 'r.bin'), 'wb') as fh:
            fh.write(bytes(w * 8))

        class Rpc:
            def screenshot(self):
                return analogrun.elkrpc.Screen(w, 64, lit)
        s = analogrun.Session.__new__(analogrun.Session)
        s.out, s.refs, s.record_refs, s.rpc = d, d, False, Rpc()
        s.step('expect-screen r rows=4-63')            # before the wait, too
        s.step('expect-screen r 0 rows=10-20')
        with self.assertRaisesRegex(AssertionError, r'rows 3-3.*comparing rows 0-5'):
            s.step('expect-screen r rows=0-5 0')
        self.assertEqual(sorted(os.listdir(d)), ['r-diff.png', 'r-got.png', 'r.bin'])
        with self.assertRaisesRegex(ValueError, 'rows=A-B'):
            s.step('expect-screen r rows=9-2')


class OffRigTest(unittest.TestCase):
    """Without the marker file the runner writes wherever it is told."""

    def setUp(self):
        saved, testrules.enforced = testrules.enforced, lambda: False
        self.addCleanup(setattr, testrules, 'enforced', saved)
        self.unit = unit = FakeUnit()

        class Port(FakeUnit):
            def __new__(cls, *_name):
                return unit
        saved_port, analogrun.open_port = analogrun.open_port, Port
        self.addCleanup(setattr, analogrun, 'open_port', saved_port)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_writes_anywhere_and_names_only_on_request(self):
        out = Path(self.tmp.name, 'rt')
        self.assertEqual(analogrun.main(['data', 'roundtrip', '/projects/1', str(out),
                                         '--to', '/projects/5', '--yes']), 0)
        self.assertEqual(self.unit.store['/projects/5'], self.unit.store['/projects/1'])
        self.assertNotIn('/projects/5', self.unit.names)
        self.assertEqual(analogrun.main(['data', 'roundtrip', '/projects/1', str(out),
                                         '--to', '/projects/6', '--name', 'my copy', '--yes']), 0)
        self.assertEqual(self.unit.names['/projects/6'], 'MY COPY')
        self.unit.kit = [make_kit('KIT A')]
        self.assertEqual(analogrun.main(['kit-roundtrip', str(out), '--kit', '0', '--settle', '0']), 0)
        self.assertEqual(analogrun.main(['data', 'clear', '/projects/1', str(out / 'b.bin'), '--yes']), 0)

    def test_any_numbered_path(self):
        self.assertEqual(testrules.split_path('/patterns/3'), ('/patterns', 3))
        with self.assertRaises(testrules.RuleError):
            testrules.split_path('/projects')

    def test_marker_path(self):
        self.assertTrue(testrules.marker_path().endswith(os.path.join('analoghealth', 'test-session-rules')))


class RulesTest(unittest.TestCase):
    def test_names(self):
        self.assertEqual(testrules.check_new_name('~t kit a'), '~T KIT A')
        for bad in ('KIT A', '~T', '~T ', '~T ' + 'X' * 13, '~T K_1', '~TKIT'):
            with self.assertRaises(testrules.RuleError):
                testrules.check_new_name(bad)
        self.assertTrue(testrules.is_test_name('~T X'))
        self.assertFalse(testrules.is_test_name('T X'))

    def test_paths(self):
        self.assertEqual(testrules.split_path('/soundbanks/C/7'), ('/soundbanks/C', 7))
        for bad in ('/samples/x', '/projects', '/projects/a', '/soundbanks/Q/1'):
            with self.assertRaises(testrules.RuleError):
                testrules.split_path(bad)

    def test_renamed_kit_dump(self):
        dump = make_kit('KIT 1')
        data = dump[10:-5]
        dump = dump[:10] + data + bytes([(sum(data) & 0x3FFF) >> 7, sum(data) & 0x7F]) + dump[-3:]
        new = testrules.renamed_kit_dump(dump, '~T KIT 1')
        self.assertEqual(testrules.kit_name(new), '~T KIT 1')
        cs = sum(new[10:-5]) & 0x3FFF
        self.assertEqual((new[-5] << 7) | new[-4], cs)
        self.assertEqual(new[-3:], dump[-3:])

    def test_kit_name(self):
        self.assertEqual(testrules.kit_name(make_kit('~T KIT 1')), '~T KIT 1')
        with self.assertRaises(testrules.RuleError):
            testrules.kit_name(b'\xF0\x7E\xF7')


if __name__ == '__main__':
    unittest.main()
