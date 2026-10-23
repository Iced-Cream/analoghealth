# SPDX-License-Identifier: GPL-2.0-or-later
"""An emulator's USB MIDI port over TCP, in place of a MIDI port: `--port tcp:HOST:PORT`.

analog-emu (--usb-midi-tcp ADDR) and digiemu's USB MIDI server carry the port as a raw MIDI
byte stream both ways, so a host without virtual MIDI ports (Windows) needs no loopMIDI, and
each emulator can have an address of its own. Same interface as winmidi.Port.
"""
import socket
import threading
import time

PREFIX = 'tcp:'


def is_address(name):
    return bool(name) and name.lower().startswith(PREFIX)


class Port:
    def __init__(self, address):
        host, _, port = address[len(PREFIX):].rpartition(':')
        try:
            self.sock = socket.create_connection((host or '127.0.0.1', int(port)), timeout=5)
        except (OSError, ValueError) as e:
            raise OSError('cannot connect to %s: %s' % (address, e))
        self.sock.settimeout(None)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.name = address
        self.msgs, self.partial = [], None
        self.cond = threading.Condition()
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        try:
            while True:
                chunk = self.sock.recv(65536)
                if not chunk:
                    return
                with self.cond:
                    for b in chunk:
                        if b == 0xF0:
                            self.partial = bytearray([b])
                        elif self.partial is not None:
                            self.partial.append(b)
                            if b == 0xF7:
                                self.msgs.append(bytes(self.partial))
                                self.partial = None
                    self.cond.notify_all()
        except OSError:
            return

    def send(self, data):
        self.sock.sendall(bytes(data))

    def send_short(self, *data):
        """A channel message (note, CC): up to three bytes."""
        self.sock.sendall(bytes(data))

    def receive(self, seconds=1.0, until=None):
        """-> list of SysEx messages received within `seconds` (stops early once
        `until(msgs)` is true), as winmidi.Port.receive."""
        out, end = [], time.time() + seconds
        while True:
            with self.cond:
                out += self.msgs
                self.msgs = []
                left = end - time.time()
                if (until and until(out)) or left <= 0:
                    return out
                self.cond.wait(min(left, 0.05))

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass
