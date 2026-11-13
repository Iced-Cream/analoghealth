# SPDX-License-Identifier: GPL-2.0-or-later
"""Which unit is on the far end of a DIN MIDI interface: a read-only probe sent out the
interface's MIDI OUT (into a unit's DIN MIDI IN), answered on its MIDI IN (from the unit's DIN
MIDI OUT). elkbench runs it before the first DIN step of a run, so a cable moved between units
needs no config change, and a step never goes to the wrong unit.

    python dinprobe.py PORT [--want a4|rytm|...] [--json] [--all]

PORT is the interface (a part of its name, IN,OUT, or an emulator's tcp:HOST:PORT). Probes, in
order, each read-only:
    rpc       Elektron RPC Ping (F0 00 20 3C 10 00 ... cmd 01): the product id, as over USB
    identity  MIDI Universal Identity Request (F0 7E 7F 06 01 F7): Elektron's id 00 20 3C
The first that names a known unit answers. --all sends every probe and prints each raw reply
(for finding out which ones a unit answers over DIN). Exit 0 when a unit answered (and it is
--want, when given), 3 when it answered as another unit, 2 when nothing answered.
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import elkrpc  # noqa: E402

# RPC Ping product id -> elkbench's unit name. 0x10 / 0x0E as analogrun's DUMP_DEVICES.
PRODUCTS = {0x10: 'rytm', 0x0E: 'a4', 0x0C: 'digitakt', 0x0D: 'digitone'}
IDENTITY_REQUEST = bytes([0xF0, 0x7E, 0x7F, 0x06, 0x01, 0xF7])
ELEKTRON_ID = bytes([0x00, 0x20, 0x3C])
EXIT_OTHER, EXIT_NONE = 3, 2


def parse_identity(m):
    """An Identity Reply (F0 7E dev 06 02 ID3 fam fam mem mem ver ver ver ver F7) from Elektron
    -> (family, member, version bytes), else None."""
    m = bytes(m)
    if len(m) < 15 or m[:2] != b'\xF0\x7E' or m[3:5] != b'\x06\x02' or m[5:8] != ELEKTRON_ID:
        return None
    fam = m[8] | m[9] << 7
    mem = m[10] | m[11] << 7
    return fam, mem, m[12:16].hex(' ')


def probe_rpc(port, timeout):
    rpc = elkrpc.Rpc(port)
    try:
        pid, name = rpc.ping(timeout=timeout)
    except TimeoutError:
        return None
    return {'probe': 'rpc', 'product': pid, 'name': name, 'unit': PRODUCTS.get(pid)}


def probe_identity(port, timeout):
    port.send(IDENTITY_REQUEST)
    got = []

    def answered(msgs):
        return any(parse_identity(m) for m in msgs)
    got = port.receive(timeout, until=answered)
    for m in got:
        r = parse_identity(m)
        if r:
            # Which family/member number is which unit is not documented: the reply proves an
            # Elektron unit is there, and names it only where IDENTITY_UNITS knows the pair.
            fam, mem, ver = r
            return {'probe': 'identity', 'family': fam, 'member': mem, 'version': ver,
                    'raw': bytes(m).hex(' '), 'unit': IDENTITY_UNITS.get((fam, mem))}
    return None


# (family, member) -> unit, filled from replies seen on units/emulators.
IDENTITY_UNITS = {}
PROBES = (('rpc', probe_rpc), ('identity', probe_identity))


def probe(port, timeout=1.5, every=False):
    """-> list of answers (dicts with 'unit', the unit's name or None). Stops at the first that
    names a unit unless every=True."""
    out = []
    for name, fn in PROBES:
        port.receive(0.05)                       # drop what came before
        try:
            r = fn(port, timeout)
        except OSError as e:
            r = {'probe': name, 'error': str(e), 'unit': None}
        if r:
            out.append(r)
            if r.get('unit') and not every:
                break
    return out


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
    ap.add_argument('--want', help='the unit that should answer (a4, rytm, ...)')
    ap.add_argument('--timeout', type=float, default=1.5, help='s to wait for each probe')
    ap.add_argument('--all', action='store_true', help='send every probe, print each reply')
    ap.add_argument('--json', action='store_true', help='one JSON line: {"unit", "answers", "at"}')
    args = ap.parse_args(argv)
    port = open_port(args.port)
    try:
        answers = probe(port, args.timeout, every=args.all)
    finally:
        port.close()
    unit = next((a['unit'] for a in answers if a.get('unit')), None)
    if args.json:
        print(json.dumps({'unit': unit, 'answers': answers, 'at': time.strftime('%Y-%m-%dT%H:%M:%S')}))
    else:
        for a in answers:
            print('  %s: %s' % (a['probe'], ', '.join('%s=%s' % kv for kv in a.items() if kv[0] != 'probe')))
        print('answered: %s' % (unit or 'nothing that names a unit'))
    if not unit:
        return EXIT_NONE
    return EXIT_OTHER if args.want and unit != args.want.lower() else 0


if __name__ == '__main__':
    sys.exit(main())
