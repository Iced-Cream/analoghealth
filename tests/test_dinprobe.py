# SPDX-License-Identifier: GPL-2.0-or-later
"""dinprobe against stand-in units on TCP ports (as an emulator's DIN MIDI): no hardware."""
import json
import os
import socket
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'tools'))
os.environ.setdefault('ANALOGHEALTH_POLICY', '')
import analogrun  # noqa: E402
import dinprobe  # noqa: E402


def fake_unit(device=None):
    """A TCP port that answers a working-kit dump request for `device` (None: nothing) as stock
    does on DIN: a ~2.8 KB kit dump, in pieces -> (address, list of what it received)."""
    srv = socket.socket()
    srv.bind(('127.0.0.1', 0))
    srv.listen(1)
    seen = []

    def serve():
        c, _ = srv.accept()
        buf = b''
        while True:
            d = c.recv(4096)
            if not d:
                return
            buf += d
            while b'\xf7' in buf:
                i = buf.index(b'\xf7') + 1
                m, buf = buf[:i], buf[i:]
                seen.append(m)
                if device is not None and m == dinprobe.request(device):
                    dump = bytes([0xF0, 0x00, 0x20, 0x3C, device, 0x00, 0x58, 1, 1]) + bytes(2760) + b'\xf7'
                    for k in range(0, len(dump), 512):
                        c.sendall(dump[k:k + 512])
    threading.Thread(target=serve, daemon=True).start()
    return 'tcp:127.0.0.1:%d' % srv.getsockname()[1], seen


class DinProbe(unittest.TestCase):
    def test_cli(self):
        self.assertEqual(dinprobe.main([fake_unit(0x06)[0], '--want', 'a4', '--timeout', '0.5']), 0)
        self.assertEqual(dinprobe.main([fake_unit(0x07)[0], '--want', 'a4', '--timeout', '0.5']), dinprobe.EXIT_NONE)
        self.assertEqual(dinprobe.main([fake_unit()[0], '--want', 'rytm', '--timeout', '0.3']), dinprobe.EXIT_NONE)

    def test_reply_device(self):
        self.assertEqual(dinprobe.reply_device(bytes([0xF0, 0, 0x20, 0x3C, 6, 0, 0x58, 1, 1, 0xF7])), 6)
        self.assertIsNone(dinprobe.reply_device(dinprobe.request(6)))          # an echoed request is no answer
        self.assertIsNone(dinprobe.reply_device(bytes([0xF0, 0x7E, 0, 0x7C, 0, 0xF7])))   # the Rytm's Wait

    def test_lazy_din_picks_where_the_unit_answers(self):
        rytm, rytm_seen = fake_unit(0x07)
        a4, a4_seen = fake_unit(0x06)
        result = os.path.join(tempfile.mkdtemp(), 'din.json')
        lazy = dinprobe.LazyDin([rytm, a4], analogrun.open_port, result, device=lambda: 0x06)
        self.assertEqual((rytm_seen, a4_seen), ([], []))            # nothing sent before the first DIN step
        tap = analogrun.Tap(lazy)
        tap.listen()                                                   # a capture step's start
        tap.send(b'\xf0\x00\x20\x3c\x06\x00\x62\x01\x01\x00\x00\x00\x00\x05\xf7')
        self.assertEqual(lazy.name, a4)
        with open(result) as fh:
            self.assertEqual(json.load(fh)['port'], a4)
        got = tap.receive(1.0)
        self.assertEqual(len(got), 0)                # the stand-in answers only the probe; its dump was drained
        self.assertEqual(rytm_seen, [dinprobe.request(6)])            # the Rytm got only the A4's request
        lazy.close()

    def test_lazy_din_sends_nothing_when_no_one_answers(self):
        rytm, seen = fake_unit(0x07)
        result = os.path.join(tempfile.mkdtemp(), 'din.json')
        lazy = dinprobe.LazyDin([rytm], analogrun.open_port, result, device=lambda: 0x06)
        with self.assertRaisesRegex(dinprobe.NoDinUnit, 'no interface answered as the a4'):
            lazy.send(b'\xf0\x01\xf7')
        self.assertEqual(seen, [dinprobe.request(6)])
        with open(result) as fh:
            self.assertIsNone(json.load(fh)['port'])

    def test_session_gives_the_lazy_din_its_device(self):
        class Quiet:
            def send(self, data):
                pass

            def receive(self, seconds=1.0, until=None):
                return []

            def close(self):
                pass
        lazy = dinprobe.LazyDin(['x'], lambda name: Quiet(), None)
        s = analogrun.Session(Quiet(), '.', lazy)
        s._device = 0x07                     # as RPC Ping on USB would set it
        self.assertEqual(lazy.device(), 0x07)


if __name__ == '__main__':
    unittest.main()
