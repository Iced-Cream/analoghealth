# SPDX-License-Identifier: GPL-2.0-or-later
"""Which unit is on the far end of a DIN MIDI interface. A cable can be moved from unit to unit,
so a run's DIN steps go to an interface only after the unit has answered there.

The probe is a dump request for the working kit, addressed to one unit's SysEx device byte (06 the
A4, 07 the Rytm), sent out of the interface's MIDI OUT into the unit's DIN MIDI IN. Stock A4 1.56
and Rytm 1.74 answer it on their DIN MIDI OUT (the emulators showed it, 2026-11-13); they answer
neither Elektron RPC Ping nor the MIDI Identity Request there. Only the reply's header (its device
byte) is read; the kit data is drained and dropped, never saved or shown. A dump request is a SysEx
dump in the test-session rules, so the probe runs at a run's first DIN step (LazyDin), after its
setup has made a ~T project active, never before.

    python dinprobe.py PORT --want a4|rytm [--json]    one interface (the hook asks for its claim)

analogrun and testplan take --din "PORT1;PORT2;..." --din-probe RESULT.json: the DIN port is then
a LazyDin, which probes the candidates in order at the first DIN step, keeps the first where the
unit answers, closes the rest, and writes RESULT.json ({"port", "unit", "device", "time"}, or
"port": null when none answered: the step then fails with nothing sent).
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ELEKTRON = bytes([0xF0, 0x00, 0x20, 0x3C])
DEVICES = {'a4': 0x06, 'rytm': 0x07}            # SysEx device byte of each unit's dumps
UNIT_OF = {v: k for k, v in DEVICES.items()}
WORK_KIT_REQUEST = 0x68
EXIT_OTHER, EXIT_NONE = 3, 2


def request(device):
    """The working kit's dump request for `device` (analogrun.dump_request's bytes)."""
    return bytes([0xF0, 0x00, 0x20, 0x3C, device, 0x00, WORK_KIT_REQUEST, 0x01, 0x01, 0x00,
                  0x00, 0x00, 0x00, 0x05, 0xF7])


def reply_device(m):
    """A dump reply's device byte (not a request: those carry a request id >= 0x60), else None."""
    m = bytes(m)
    if len(m) > 7 and m.startswith(ELEKTRON) and m[5] == 0x00 and 0x50 <= m[6] < 0x60:
        return m[4]
    return None


def drain(port, quiet=0.3, limit=3.0):
    """Read and drop what the port sends until it has been quiet for `quiet` s."""
    end = time.monotonic() + limit
    while time.monotonic() < end:
        if not port.receive(quiet):
            return
    if hasattr(port, 'take_short'):
        port.take_short()


def probe(port, device, timeout=3.0):
    """Send the probe for `device` -> the device byte that answered (`device`), or None. Leaves
    the port drained: the reply's data never reaches a later capture."""
    drain(port, 0.1, 1.0)                         # what came before
    port.send(request(device))
    got = []

    def answered(msgs):
        return any(reply_device(m) is not None for m in msgs)
    got = port.receive(timeout, until=answered)
    drain(port)
    if hasattr(port, 'take_short'):
        port.take_short()
    for m in got:
        d = reply_device(m)
        if d is not None:
            return d
    return None


class NoDinUnit(RuntimeError):
    pass


class LazyDin:
    """A DIN port that is not chosen until first used: then each candidate is opened and probed
    for the unit (device() -> its device byte, read over USB), the first that answers is kept and
    the rest closed. The same calls as a port (send, send_short, receive, take_short, close)."""

    def __init__(self, candidates, opener, result=None, device=None, say=None, short=False):
        self.candidates = [c.strip() for c in candidates if c.strip()]
        self.opener, self.result, self.device, self.say = opener, result, device, say or (lambda s: None)
        self.keeps_short = short
        self.port = None
        self.name = None

    def _write(self, rec):
        if self.result:
            with open(self.result, 'w', encoding='utf-8') as fh:
                json.dump(rec, fh)

    def resolve(self):
        if self.port is not None:
            return self.port
        if self.device is None:
            raise NoDinUnit('DIN probe: the unit\'s device byte is not known')
        dev = self.device()
        unit = UNIT_OF.get(dev, '0x%02X' % dev)
        tried = []
        for name in self.candidates:
            try:
                p = self.opener(name)
            except Exception as e:                   # noqa: BLE001 - try the next one
                tried.append('%s: %s' % (name, e))
                continue
            try:
                got = probe(p, dev)
            except Exception as e:                   # noqa: BLE001
                got = None
                tried.append('%s: %s' % (name, e))
            if got == dev:
                self.port, self.name = p, name
                if hasattr(p, 'keeps_short'):
                    self.keeps_short = p.keeps_short
                at = time.strftime('%H:%M:%S')
                self._write({'port': name, 'unit': unit, 'device': dev, 'time': time.time(), 'tried': tried})
                self.say('  DIN: probe answered %s on %s at %s' % (unit, name, at))
                return p
            tried.append('%s: no answer as the %s' % (name, unit))
            p.close()
        self._write({'port': None, 'unit': unit, 'device': dev, 'time': time.time(), 'tried': tried})
        raise NoDinUnit('DIN probe: no interface answered as the %s, nothing sent over DIN (%s)'
                        % (unit, '; '.join(tried) or 'no candidates'))

    def send(self, data):
        self.resolve().send(data)

    def send_short(self, *data):
        self.resolve().send_short(*data)

    def receive(self, seconds=1.0, until=None):
        return self.resolve().receive(seconds, until)

    def take_short(self):
        return self.resolve().take_short()

    def close(self):
        if self.port is not None:
            self.port.close()


def open_port(name):
    if sys.platform == 'win32':
        from winmidi import Port
    else:
        from midimido import Port
    return Port(name)


def main(argv=None):
    ap = argparse.ArgumentParser(prog='dinprobe.py', description=__doc__.split('\n')[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__.split('\n', 1)[1])
    ap.add_argument('port', help='the DIN interface: part of its name, IN,OUT, or tcp:HOST:PORT')
    ap.add_argument('--want', required=True, choices=sorted(DEVICES), help='the unit that should answer')
    ap.add_argument('--timeout', type=float, default=3.0)
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args(argv)
    port = open_port(args.port)
    try:
        got = probe(port, DEVICES[args.want], args.timeout)
    finally:
        port.close()
    unit = UNIT_OF.get(got) if got is not None else None
    if args.json:
        print(json.dumps({'unit': unit, 'port': args.port, 'time': time.time()}))
    else:
        print('answered: %s' % (unit or 'nothing'))
    return 0 if unit == args.want else EXIT_NONE


if __name__ == '__main__':
    sys.exit(main())
