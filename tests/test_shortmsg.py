#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Short-message capture (capture ... short): the byte parser, NRPN decoding, the
matchers, midisock's reader over a real socket, and the capture step on a fake port.
No MIDI device is opened."""
from pathlib import Path
import os
import socket
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
os.environ.setdefault('ANALOGHEALTH_POLICY', '')
import analogrun
import midisock
import shortmsg
import testplan
from tests.test_analogrun import FakeUnit, KIT, DUMP_REQUEST


def nrpn_bytes(ch, msb, lsb, val, lsb_data=True):
    s = 0xB0 | (ch - 1)
    out = [bytes([s, 99, msb]), bytes([s, 98, lsb]), bytes([s, 6, val >> 7])]
    if lsb_data:
        out.append(bytes([s, 38, val & 0x7F]))
    return out


class ParserTest(unittest.TestCase):
    def feed(self, data):
        p, out = shortmsg.Parser(), []
        for b in data:
            m = p.feed(b)
            if m:
                out.append(m)
        return out

    def test_running_status_real_time_and_strays(self):
        data = bytes([0x05,                        # stray data byte: dropped
                      0xB0, 99, 0, 98, 0x40,       # running status: two CCs
                      0xF8,                        # clock in the middle: not kept, not breaking
                      6, 1, 0xFE, 38, 0x10,
                      0xC2, 7, 8,                  # program change, running status
                      0xF6,                        # tune request (no data)
                      0x20])                       # stray after system common: dropped
        self.assertEqual(self.feed(data), [b'\xB0\x63\x00', b'\xB0\x62\x40', b'\xB0\x06\x01',
                                           b'\xB0\x26\x10', b'\xC2\x07', b'\xC2\x08', b'\xF6'])

    def test_from_word(self):
        self.assertEqual(shortmsg.from_word(0x7F3EB1), b'\xB1\x3E\x7F')
        self.assertEqual(shortmsg.from_word(0x05C3), b'\xC3\x05')
        self.assertIsNone(shortmsg.from_word(0xF8))
        self.assertEqual(shortmsg.from_word(0xFA, realtime=True), b'\xFA')
        self.assertEqual(shortmsg.from_word(0x7F3EB1, realtime=True), b'\xB1\x3E\x7F')

    def test_real_time_kept_when_asked(self):
        p, out = shortmsg.Parser(realtime=True), []
        for b in bytes([0xB0, 99, 0xF8, 0, 0xFA, 0xFB, 98, 0xFC, 0x40]):
            m = p.feed(b)
            if m:
                out.append(m)
        # real time comes out where it fell, without breaking the CC around it
        self.assertEqual(out, [b'\xF8', b'\xB0\x63\x00', b'\xFA', b'\xFB', b'\xFC', b'\xB0\x62\x40'])


class NrpnTest(unittest.TestCase):
    def test_decode(self):
        msgs = nrpn_bytes(1, 1, 80, 1000) + nrpn_bytes(2, 0, 0x70, 64 << 7, False)
        msgs.append(b'\xB0\x06\x02')                 # a second data entry, same parameter
        self.assertEqual(shortmsg.nrpns(msgs), [(1, 1, 80, 1000, True), (2, 0, 0x70, 64, False),
                                                (1, 1, 80, 2, False)])

    def test_matchers(self):
        msgs = nrpn_bytes(1, 0, 0x70, 300) + [b'\xB4\x14\x40']
        m = shortmsg.parse_matcher
        self.assertTrue(shortmsg.matches(m('nrpn=1:0:0x70'), msgs))
        self.assertTrue(shortmsg.matches(m('nrpn=1:0:112:300'), msgs))
        self.assertFalse(shortmsg.matches(m('nrpn=1:0:112:301'), msgs))
        self.assertFalse(shortmsg.matches(m('nrpn=2:0:112'), msgs))
        self.assertTrue(shortmsg.matches(m('cc=5:20'), msgs))
        self.assertTrue(shortmsg.matches(m('cc=5:20:64'), msgs))
        self.assertFalse(shortmsg.matches(m('cc=5:21'), msgs))
        self.assertIsNone(m('any'))
        for bad in ('nrpn=1:2', 'cc=17:1', 'cc=x:1', 'nrpn=1:2:3:4:5', 'rt=F7', 'rt=x', 'rt=100'):
            with self.assertRaises(ValueError):
                m(bad)

    def test_real_time_matchers_and_modes(self):
        m = shortmsg.parse_matcher
        msgs = [b'\xF8', b'\xFA', b'\xB0\x14\x40', b'\xFC']
        self.assertEqual(m('rt=fa'), ('rt', (0xFA,)))
        self.assertTrue(shortmsg.matches(m('rt=FA'), msgs))
        self.assertTrue(shortmsg.matches(m('rt=0xFC'), msgs))
        self.assertFalse(shortmsg.matches(m('rt=FB'), msgs))
        mode = shortmsg.realtime_mode
        self.assertIsNone(mode(['short', 'any']))
        self.assertEqual(mode(['realtime']), 'all')
        self.assertEqual(mode(['REALTIME=noclock', 'rt=FA'], [m('rt=FA')]), 'noclock')
        self.assertEqual(mode(['rt=FA'], [m('rt=FA')]), 'all')
        with self.assertRaises(ValueError):
            mode(['realtime=clock'])
        with self.assertRaises(ValueError):
            mode(['realtime=noclock', 'rt=F8'], [m('rt=F8')])
        st = [(0, b) for b in msgs]
        self.assertEqual([b for _, b in shortmsg.filter_realtime(st, None)], [b'\xB0\x14\x40'])
        self.assertEqual([b for _, b in shortmsg.filter_realtime(st, 'noclock')],
                         [b'\xFA', b'\xB0\x14\x40', b'\xFC'])
        self.assertEqual(shortmsg.filter_realtime(st, 'all'), st)
        self.assertIn('start', shortmsg.text([(0.0, b'\xFA')]))


class MidisockTest(unittest.TestCase):
    """A real socket in place of analog-emu's --usb-midi-tcp server."""

    def serve(self, short, realtime=False):
        srv = socket.socket()
        srv.bind(('127.0.0.1', 0))
        srv.listen(1)
        self.addCleanup(srv.close)
        port = midisock.Port('tcp:127.0.0.1:%d' % srv.getsockname()[1], short=short, realtime=realtime)
        self.addCleanup(port.close)
        conn, _ = srv.accept()
        self.addCleanup(conn.close)
        return port, conn

    def test_sysex_unchanged_and_shorts_kept_apart(self):
        stream = (b'\xB0\x63\x00\x62\x70' + KIT[0][:5] + b'\xF8' + KIT[0][5:]
                  + b'\xF8\x06\x02\x26\x0C\xF0\x7E\xF7')
        for short in (False, True):
            port, conn = self.serve(short)
            conn.sendall(stream)
            got = port.receive(1.0, until=lambda m: len(m) >= 2)
            # the real-time byte inside the SysEx stays in it, as it always did
            self.assertEqual(got, [KIT[0][:5] + b'\xF8' + KIT[0][5:], b'\xF0\x7E\xF7'])
            shorts = [m for _, m in port.take_short()]
            self.assertEqual(shorts, [b'\xB0\x63\x00', b'\xB0\x62\x70', b'\xB0\x06\x02', b'\xB0\x26\x0C']
                             if short else [])
            self.assertEqual(port.take_short(), [])

    def test_real_time_kept_apart_when_asked(self):
        stream = (b'\xFA\xB0\x63\x00' + KIT[0][:5] + b'\xF8' + KIT[0][5:] + b'\xFC\xF0\x7E\xF7')
        port, conn = self.serve(True, realtime=True)
        conn.sendall(stream)
        got = port.receive(1.0, until=lambda m: len(m) >= 2)
        # with real time asked for, the clock inside the SysEx is taken out of it (as winmm does)
        self.assertEqual(got, [KIT[0], b'\xF0\x7E\xF7'])
        self.assertEqual([m for _, m in port.take_short()], [b'\xFA', b'\xB0\x63\x00', b'\xF8', b'\xFC'])


class ShortPort(FakeUnit):
    """The fake unit, opened to keep short messages: `emit` queues them as the unit
    would send them (a knob turned, say)."""
    keeps_short = True
    keeps_realtime = False

    def __init__(self):
        super().__init__()
        self.queued = []

    def emit(self, msgs):
        self.queued += [(time.monotonic(), m) for m in msgs]

    def take_short(self):
        out, self.queued = self.queued, []
        return out


class CaptureStepTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.unit = ShortPort()
        self.s = analogrun.Session(self.unit, self.tmp.name)

    def test_capture_short_with_matchers(self):
        self.s.step('capture first.txt 0.05 short')       # starts listening from here
        self.unit.emit(nrpn_bytes(1, 0, 0x70, 77 << 7 | 5) + [b'\xB0\x14\x40'])
        self.s.step('capture knob.txt 0.1 any nrpn=1:0:0x70 nrpn=1:0:112:9861 cc=1:20:64')
        text = Path(self.tmp.name, 'knob.txt').read_text()
        self.assertIn('B0 63 00', text)
        self.assertIn('nrpn ch 1 0:112 = 9861', text)
        self.unit.emit(nrpn_bytes(1, 0, 0x71, 1))
        with self.assertRaisesRegex(AssertionError, r'nrpn=1:0:112.*NRPNs seen: 1:0:113'):
            self.s.step('capture miss.txt 0.1 nrpn=1:0:112')
        self.s.step('capture quiet.txt 0.05 none short')
        self.unit.emit([b'\x90\x24\x64'])
        with self.assertRaisesRegex(AssertionError, '1 short message'):
            self.s.step('capture loud.txt 0.05 none short')
        with self.assertRaises(ValueError):
            self.s.step('capture x.txt 0.05 some short')

    def test_before_the_listen_is_not_kept(self):
        self.unit.emit([b'\xB0\x14\x01'])                  # before any send or capture
        self.s.step('capture a.txt 0.05 none short')

    def test_sysex_capture_unchanged_on_a_short_port(self):
        Path(self.tmp.name, 'req.syx').write_bytes(DUMP_REQUEST)
        self.s.step('send req.syx')
        self.unit.emit([b'\xB0\x14\x40'])
        self.s.step('capture k.txt 0.05 any short')        # does not take the SysEx reply
        self.s.step('capture reply.syx 0.1 any')
        self.assertEqual(analogrun.load_syx(Path(self.tmp.name, 'reply.syx')), KIT)

    def test_capture_real_time(self):
        self.unit.keeps_realtime = True
        self.s.step('capture first.txt 0.05 short')
        self.unit.emit([b'\xF8', b'\xFA', b'\xF8', b'\xB0\x14\x40', b'\xFC'])
        self.s.step('capture t.txt 0.1 any rt=FA rt=FC realtime=noclock')
        text = Path(self.tmp.name, 't.txt').read_text()
        self.assertIn('FA', text)
        self.assertIn('stop', text)
        self.assertNotIn('F8', text)
        self.unit.emit([b'\xF8', b'\xFB'])
        self.s.step('capture c.txt 0.1 any short realtime rt=FB rt=F8')
        self.assertIn('clock', Path(self.tmp.name, 'c.txt').read_text())
        self.unit.emit([b'\xFA', b'\xF8'])
        self.s.step('capture plain.txt 0.1 none short')      # no realtime: as it always was
        self.unit.emit([b'\xF8'])
        with self.assertRaisesRegex(AssertionError, 'rt=FA'):
            self.s.step('capture miss.txt 0.05 rt=FA')

    def test_real_time_needs_a_port_that_keeps_it(self):
        with self.assertRaisesRegex(ValueError, 'without real-time bytes'):
            self.s.step('capture a.txt 0.05 short realtime')

    def test_a_port_without_short_refuses(self):
        s = analogrun.Session(FakeUnit(), self.tmp.name)
        with self.assertRaisesRegex(ValueError, 'without short messages'):
            s.step('capture a.txt 0.05 nrpn=1:0:1')

    def test_din_leg(self):
        din = ShortPort()
        s = analogrun.Session(FakeUnit(), self.tmp.name, din)
        s.step('capture a.txt 0.05 short din')
        din.emit([b'\xB3\x14\x40'])
        s.step('capture b.txt 0.05 cc=4:20 din')


class OpenTest(unittest.TestCase):
    def test_wants_short(self):
        self.assertTrue(analogrun.wants_short('shot a.png; capture k.txt 2 short'))
        self.assertTrue(analogrun.wants_short('capture k.txt 2 nrpn=1:0:112 din'))
        self.assertTrue(analogrun.wants_short('capture k.txt 2 any CC=1:20'))
        self.assertFalse(analogrun.wants_short('capture k.syx 2 any din\n# capture x 1 short'))
        self.assertFalse(analogrun.wants_short('capture short 2'))   # a file named short
        self.assertTrue(testplan.uses_short([('a', ['capture k.txt 1 short'])]))
        self.assertFalse(testplan.uses_short([('a', ['capture k.syx 1 any din'])]))
        self.assertTrue(analogrun.wants_short('capture k.txt 2 any rt=FA'))
        self.assertTrue(analogrun.wants_realtime('capture k.txt 2 short realtime=noclock din'))
        self.assertFalse(analogrun.wants_realtime('capture k.txt 2 short'))
        self.assertTrue(testplan.uses_short([('a', ['capture k.txt 1 rt=FC'])]))
        self.assertTrue(testplan.uses_realtime([('a', ['capture k.txt 1 short realtime'])]))
        self.assertFalse(testplan.uses_realtime([('a', ['capture k.txt 1 short'])]))

    def test_do_opens_short_only_when_asked(self):
        opened = []

        def open_port(name, **kw):
            opened.append(kw)
            return ShortPort() if kw.get('short') else FakeUnit()
        saved, analogrun.open_port = analogrun.open_port, open_port
        self.addCleanup(setattr, analogrun, 'open_port', saved)
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(analogrun.main(['--quiet', 'do', 'wait 0', '--out', d]), 0)
            self.assertEqual(analogrun.main(['--quiet', 'do', 'capture a.txt 0.05 short', '--out', d]), 0)
            self.assertEqual(analogrun.main(['--quiet', 'do', 'capture a.txt 0.05 short realtime',
                                             '--out', d]), 1)    # ShortPort here keeps no real time
        self.assertEqual(opened, [{}, {'short': True}, {'short': True, 'realtime': True}])


if __name__ == '__main__':
    unittest.main()
