# SPDX-License-Identifier: GPL-2.0-or-later
"""dinprobe against a stand-in unit on a TCP port (as an emulator's DIN MIDI): no hardware."""
import os
import socket
import struct
import sys
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'tools'))
import dinprobe  # noqa: E402
import elkrpc  # noqa: E402
from analogusb import pack7, unpack7  # noqa: E402


def fake_unit(product=None, identity=None):
    """A TCP port that answers RPC Ping as `product` (None: not at all) and the Identity Request
    with `identity` (bytes, None: not at all) -> its address."""
    srv = socket.socket()
    srv.bind(('127.0.0.1', 0))
    srv.listen(1)

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
                if m == dinprobe.IDENTITY_REQUEST and identity:
                    c.sendall(identity)
                elif elkrpc.parse(m) is not None and product is not None:
                    seq = struct.unpack_from('>H', unpack7(m[6:-1]))[0]
                    args = bytes([product, 0]) + b'Unit\0'
                    c.sendall(elkrpc.HEADER + pack7(struct.pack('>HHB', 1, seq, 0x81) + args) + b'\xf7')
    threading.Thread(target=serve, daemon=True).start()
    return 'tcp:127.0.0.1:%d' % srv.getsockname()[1]


class DinProbe(unittest.TestCase):
    def test_rpc_names_the_unit(self):
        self.assertEqual(dinprobe.main([fake_unit(0x0E), '--want', 'a4', '--timeout', '0.5']), 0)
        self.assertEqual(dinprobe.main([fake_unit(0x10), '--want', 'a4', '--timeout', '0.5']), dinprobe.EXIT_OTHER)

    def test_nothing_answers(self):
        self.assertEqual(dinprobe.main([fake_unit(), '--timeout', '0.3']), dinprobe.EXIT_NONE)

    def test_identity_reply_is_read(self):
        reply = bytes([0xF0, 0x7E, 0x00, 0x06, 0x02, 0x00, 0x20, 0x3C, 0x05, 0x00, 0x0E, 0x00, 1, 5, 6, 0, 0xF7])
        self.assertEqual(dinprobe.parse_identity(reply)[:2], (5, 14))
        port = dinprobe.open_port(fake_unit(identity=reply))
        try:
            answers = dinprobe.probe(port, 0.3, every=True)
        finally:
            port.close()
        self.assertEqual([a['probe'] for a in answers], ['identity'])
        self.assertIsNone(answers[0]['unit'])           # an Elektron unit, but not one named by its numbers


if __name__ == '__main__':
    unittest.main()
