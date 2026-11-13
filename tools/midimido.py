# SPDX-License-Identifier: GPL-2.0-or-later
"""Mido transport shared by diagnostics and keys; adapted from the measurement runner."""
import collections
import time

import shortmsg


class Port:
    def __new__(cls, name=None, short=False, realtime=False):
        import midisock
        if midisock.is_address(name):           # an emulator over TCP: no MIDI ports
            return midisock.Port(name, short=short, realtime=realtime)
        return super().__new__(cls)

    def __init__(self, name=None, short=False, realtime=False):
        import mido
        self.mido = mido
        # short=True also keeps notes, CCs and NRPNs (not real time), for take_short()
        self.keeps_short = short
        # realtime=True (with short) keeps real-time bytes F8-FF too, each a one-byte message
        self.keeps_realtime = short and realtime
        self.shorts = collections.deque(maxlen=20000)
        # NAME is part of both port names, or 'IN,OUT' names them apart.
        pair = [p.strip() for p in name.lower().split(',', 1)] if name and ',' in name else None
        wanted = (name.lower(),) if name else ('analog rytm', 'analog four')

        def choose(names, side=None):
            want = (pair[side],) if pair else wanted
            matches = [n for n in names if n.lower() in want] or [
                n for n in names if any(w in n.lower() for w in want)]
            if len(matches) != 1:
                raise OSError('Expected one Analog Rytm MKII or Analog Four MKII port (use --port '
                              'NAME when both are connected); found %r. Available: %r'
                              % (matches, names))
            return matches[0]

        self.incoming = mido.open_input(choose(mido.get_input_names(), 0))
        try:
            self.outgoing = mido.open_output(choose(mido.get_output_names(), 1))
        except Exception:
            self.incoming.close()
            raise

    def send(self, data):
        self.outgoing.send(self.mido.Message.from_bytes(data))

    def send_short(self, *data):
        self.outgoing.send(self.mido.Message.from_bytes(list(data)))

    def receive(self, seconds=1.0, until=None):
        messages = []
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            for message in self.incoming.iter_pending():
                if message.type == 'sysex':
                    messages.append(bytes(message.bytes()))
                elif self.keeps_short:
                    m = bytes(message.bytes())
                    if m and (m[0] < 0xF8 or self.keeps_realtime):
                        self.shorts.append(shortmsg.stamp(m))
            if until and until(messages):
                break
            time.sleep(0.002)
        return messages

    def take_short(self):
        """-> [(monotonic time, bytes)] of the short messages since the last call (read
        from the port during receive())."""
        out = list(self.shorts)
        self.shorts.clear()
        return out

    def close(self):
        self.incoming.close()
        self.outgoing.close()
