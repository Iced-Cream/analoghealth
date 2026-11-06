#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""peekset.py: the plan steps peek-set and peek-grown against a fake memory."""
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'tools'))
import peekset  # noqa: E402

BASE = 0x41000000


class Mem:
    def __init__(self):
        self.m = {}
        self.reads = []

    def put(self, addr, data):
        for i, b in enumerate(data):
            self.m[addr + i] = b

    def read(self, addr, n):
        self.reads.append((addr, n))
        return bytes(self.m.get(addr + i, 0) for i in range(n))


def readable(addr, n):
    return 0x40000000 <= addr and addr + n <= 0x48000000


class PeekSet(unittest.TestCase):
    def test_items(self):
        self.assertEqual(peekset.parse_item('0x40285b90:32'), (None, 0x40285b90, 32))
        self.assertEqual(peekset.parse_item('[0x419208b8]+0x48:16'), (0x419208b8, 0x48, 16))
        self.assertEqual(peekset.parse_item('[0x40285b98]:131072'), (0x40285b98, 0, 131072))
        self.assertEqual(peekset.parse_item('[0x40000010]-8:4'), (0x40000010, -8, 4))
        for bad in ('0x10', '[0x10:4', '0x10:0', 'x:4', '[0x10]+:4'):
            with self.assertRaises(ValueError):
                peekset.parse_item(bad)

    def test_pointer_and_outside(self):
        m = Mem()
        m.put(0x40000100, struct.pack('>I', 0x40000200))
        m.put(0x40000208, b'ABCD')
        m.put(0x40000300, struct.pack('>I', 0x10))            # leads outside DDR
        items = [peekset.parse_item(w) for w in ('0x40000100:4', '[0x40000100]+8:4', '[0x40000300]:4')]
        blob, notes = peekset.run_set(m.read, readable, items)
        r = peekset.records(blob)
        self.assertEqual([(i, a, d) for i, a, _, d in r],
                         [(0, 0x40000100, struct.pack('>I', 0x40000200)), (1, 0x40000208, b'ABCD'),
                          (2, 0x10, b'')])
        self.assertEqual(len(notes), 1)
        self.assertNotIn((0x10, 4), m.reads)                   # never read outside


class PeekGrown(unittest.TestCase):
    def test_grown_units(self):
        a = bytes([0b00000001, 0, 0])
        b = bytes([0b00000101, 0b10000000, 0b1])
        c = bytes([0b00000101, 0b10000000, 0])                 # unit 16 freed again by C
        self.assertEqual(peekset.grown_units(a, b, c), [2, 15])
        self.assertEqual(peekset.grown_units(a, a, a), [])

    def test_spans_merge(self):
        self.assertEqual(peekset.spans([10, 20, 1000]), [(2, 19), (992, 9)])
        self.assertEqual(peekset.spans([3]), [(0, 4)])

    def test_run_grown_reads_and_caps(self):
        m = Mem()
        a = bytes(64)
        b = bytearray(64)
        b[0] |= 1 << 3                                         # unit 3
        b[40] |= 1                                             # unit 320
        c = bytes(b)
        m.put(BASE + 3 * 16, b'\x40\x1c\xb5\x4c')
        blob, notes, n = peekset.run_grown(m.read, readable, a, bytes(b), c, BASE, 4096)
        self.assertEqual(n, 2)
        r = peekset.records(blob)
        self.assertEqual([(x[1], len(x[3])) for x in r], [(BASE, 64), (BASE + 312 * 16, 144)])
        self.assertEqual(r[0][3][48:52], b'\x40\x1c\xb5\x4c')
        blob, notes, n = peekset.run_grown(m.read, readable, a, bytes(b), c, BASE, 100)
        self.assertEqual([len(x[3]) for x in peekset.records(blob)], [64, 36])
        blob, notes, n = peekset.run_grown(m.read, readable, a, bytes(b), c, BASE, 64)
        self.assertEqual(len(peekset.records(blob)), 1)
        self.assertTrue(any('MAXBYTES' in x for x in notes))


if __name__ == '__main__':
    unittest.main()
