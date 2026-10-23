# SPDX-License-Identifier: GPL-2.0-or-later
"""One program on the Rytm's USB MIDI port: keys, screenshots, notes and CCs,
SysEx dumps and the data store, in one session, so a step can look at the
screen while a key is held.

    python rytmrun.py shot OUT.png [--scale N]       the screen now (stock OS too)
    python rytmrun.py run SCRIPT [--out DIR]         steps from a file ('-' = stdin)
    python rytmrun.py do 'hold FUNC; shot f.png; release FUNC'
    python rytmrun.py dump-capture OUT.syx [--keys SEQ] [--request HEX] [--quiet S]
    python rytmrun.py kit-roundtrip DIR --keys SEQ [--receive-keys SEQ] [--request HEX]
    python rytmrun.py info                           product, OS version
    python rytmrun.py data ls PATH
    python rytmrun.py data get PATH OUT
    python rytmrun.py data put PATH IN --yes         replaces the object on the unit
    python rytmrun.py data roundtrip PATH DIR [--to PATH2] --yes

Steps (one per line or separated by ';'; '#' starts a comment):

    tap KEY [MS]          press and release (rytmhealth build)
    hold KEY [MS]         press; the firmware lets go after MS (default its maximum, 2 s)
    release KEY
    keys SEQ              as rytmusb.py keys: 'GLOBAL,DOWN,FUNC(YES)'
    turn ENC DELTA [STEP] encoder turn, if the rytmhealth build has them
    shot FILE             screenshot: .png (4x) or .bin (raw framebuffer)
    expect-screen FILE    stop unless the screen equals a saved .bin
    cc CH NUM VAL         MIDI channel 1-16
    nrpn CH MSB LSB VAL   VAL 0-16383 (CC 99, 98, 6, 38)
    note CH NOTE VEL [MS] note on, off after MS (default 100)
    wait S
    stats                 one rytmhealth CPU/DSP/heap line
    dump ADDR LEN FILE    DDR to a file (rytmhealth)
    say TEXT              print TEXT

Key steps open a rytmhealth key session on first use and close it (releasing
any synthetic key) at the end; everything else works on a stock OS. Each
command here is the only user of the port while it runs: close Transfer,
Overbridge's MIDI, screendump and rytmusb.py first.
"""
import argparse
import os
import shlex
import struct
import sys
import time

import elkrpc
from rytmusb import Diag, RemoteKeys, decode_stats, key_sequence, peek, req_stats

ELEKTRON = bytes([0xF0, 0x00, 0x20, 0x3C])


class Tap:
    """The Port, keeping every SysEx that arrives so a dump sent while another
    request waits for its reply is not lost."""

    def __init__(self, port):
        self.port = port
        self.seen = []

    def send(self, data):
        self.port.send(data)

    def send_short(self, *data):
        self.port.send_short(*data)

    def receive(self, seconds=1.0, until=None):
        msgs = self.port.receive(seconds, until)
        self.seen.extend(msgs)
        return msgs


def is_dump(m):
    """Elektron SysEx that is neither the RPC channel nor rytmhealth's."""
    return m.startswith(ELEKTRON) and not m.startswith(elkrpc.HEADER) and m[4:6] != b'\x7d\x00'


class Session:
    def __init__(self, port, out='.'):
        self.port = Tap(port)
        self.rpc = elkrpc.Rpc(self.port)
        self.diag = Diag(self.port)
        self.out = out
        self._keys = None

    @property
    def keys(self):
        if self._keys is None:
            self._keys = RemoteKeys(self.diag)
        return self._keys

    def close(self):
        if self._keys is not None:
            self._keys.cancel()
            self._keys = None

    def path(self, name):
        return name if os.path.isabs(name) else os.path.join(self.out, name)

    # --- steps ----------------------------------------------------------------

    def shot(self, name, scale=4):
        screen = self.rpc.screenshot()
        with open(self.path(name), 'wb') as fh:
            fh.write(screen.pixels if name.endswith('.bin') else screen.png(scale))
        return screen

    def cc(self, ch, num, val):
        self.port.send_short(0xB0 | (ch - 1), num, val)

    def nrpn(self, ch, msb, lsb, val):
        for num, v in ((99, msb), (98, lsb), (6, val >> 7), (38, val & 0x7F)):
            self.cc(ch, num, v)

    def note(self, ch, note, vel, ms=100):
        self.port.send_short(0x90 | (ch - 1), note, vel)
        time.sleep(ms / 1000)
        self.port.send_short(0x80 | (ch - 1), note, 0)

    def key_steps(self, text):
        for hold, tap in key_sequence(text):
            if hold:
                self.keys.press(hold)
            self.keys.tap(tap)
            if hold:
                self.keys.release(hold)

    def step(self, line):
        words = shlex.split(line, comments=True)
        if not words:
            return
        op, a = words[0].lower(), words[1:]
        num = lambda s: int(s, 0)
        if op == 'tap':
            self.keys.tap(a[0], num(a[1]) if len(a) > 1 else None)
        elif op == 'hold':
            self.keys.press(a[0], num(a[1]) if len(a) > 1 else None)
        elif op == 'release':
            self.keys.release(a[0])
        elif op == 'keys':
            self.key_steps(' '.join(a))
        elif op == 'turn':
            if not hasattr(self.keys, 'turn'):
                raise ValueError('this rytmusb.py has no encoder turns')
            self.keys.turn(a[0].upper(), num(a[1]), num(a[2]) if len(a) > 2 else 1)
        elif op == 'shot':
            s = self.shot(a[0])
            print('  %s: %dx%d, %d lit' % (a[0], s.w, s.h, s.lit()))
        elif op == 'expect-screen':
            with open(self.path(a[0]), 'rb') as fh:
                want = fh.read()
            got = self.rpc.screenshot()
            if got.pixels != want:
                self.shot(os.path.splitext(a[0])[0] + '-got.png')
                raise AssertionError('screen differs from %s (%d of %d bytes)' % (
                    a[0], sum(x != y for x, y in zip(got.pixels, want)), len(want)))
        elif op == 'cc':
            self.cc(*map(num, a))
        elif op == 'nrpn':
            self.nrpn(*map(num, a))
        elif op == 'note':
            self.note(*map(num, a))
        elif op == 'wait':
            time.sleep(float(a[0]))
        elif op == 'stats':
            s = decode_stats(self.diag.call(req_stats))
            print('  second %d: CPU %.1f %%, DSP %.1f %% (peak %.1f %%), heap free %d' % (
                s['seconds'], 100 * s.get('cpu', 0), 100 * s.get('dsp', 0),
                100 * s.get('dsp_peak', 0), s['heap_free']))
        elif op == 'dump':
            data = peek(self.diag, num(a[0]), num(a[1]))
            with open(self.path(a[2]), 'wb') as fh:
                fh.write(data)
        elif op == 'say':
            print(' '.join(a))
        else:
            raise ValueError('unknown step %r' % op)

    def run(self, text):
        lines = [s for line in text.splitlines() for s in line.split(';')]
        for n, line in enumerate(lines, 1):
            if line.strip() and not line.strip().startswith('#'):
                print('%3d  %s' % (n, line.strip()))
                self.step(line)

    # --- SysEx dumps ------------------------------------------------------------

    def capture(self, keys=None, request=None, wait=30.0, quiet=1.0):
        """Start a dump (keys on the unit, a raw request, or a person) and collect
        it: every Elektron dump message until none has come for `quiet` s."""
        self.port.seen.clear()
        if request:
            self.port.send(request)
        if keys:
            self.key_steps(keys)
        if not keys and not request:
            print('start the SysEx send on the unit now (waiting %g s)' % wait)
        end, last = time.monotonic() + wait, None
        while True:
            self.port.receive(0.05)
            dumps = [m for m in self.port.seen if is_dump(m)]
            if dumps and last != len(dumps):
                last, quiet_end = len(dumps), time.monotonic() + quiet
            if dumps and time.monotonic() >= quiet_end:
                return dumps
            if not dumps and time.monotonic() >= end:
                raise TimeoutError('no dump arrived')

    def send_dump(self, msgs, gap=0.05):
        for m in msgs:
            self.port.send(m)
            time.sleep(gap)


def save(path, data):
    with open(path, 'wb') as fh:
        fh.write(data)


def save_syx(path, msgs):
    save(path, b''.join(msgs))


def load_syx(path):
    with open(path, 'rb') as fh:
        data = fh.read()
    out, at = [], 0
    while True:
        s = data.find(b'\xF0', at)
        e = data.find(b'\xF7', s)
        if s < 0 or e < 0:
            return out
        out.append(data[s:e + 1])
        at = e + 1


def compare(a, b):
    """-> list of (message, offset) where two dumps differ, or [] if equal."""
    if len(a) != len(b):
        return [('count', len(a), len(b))]
    diffs = []
    for i, (x, y) in enumerate(zip(a, b)):
        if len(x) != len(y):
            diffs.append((i, 'length', len(x), len(y)))
        else:
            diffs += [(i, k) for k in range(len(x)) if x[k] != y[k]]
    return diffs


def backup(s, path, out):
    """Save what `path` holds now before it is replaced (nothing if it is empty)."""
    try:
        save(out, s.rpc.data_read(path))
        print('saved the current %s to %s' % (path, out))
    except elkrpc.RpcError:
        print('%s holds nothing to back up' % path)


def open_port():
    if sys.platform == 'win32':
        from winmidi import Port
    else:
        from midimido import Port
    return Port()


def progress(frac):
    print('\r  %5.1f %%' % (100 * frac), end='', flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser(prog='rytmrun.py', description=__doc__.split('\n')[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__.split('\n', 1)[1])
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('shot')
    p.add_argument('out')
    p.add_argument('--scale', type=int, default=4)
    p = sub.add_parser('run')
    p.add_argument('script')
    p.add_argument('--out', default='.')
    p = sub.add_parser('do')
    p.add_argument('steps')
    p.add_argument('--out', default='.')
    sub.add_parser('info')
    p = sub.add_parser('dump-capture')
    p.add_argument('out')
    p.add_argument('--keys', help='key sequence that starts SYSEX SEND on the unit')
    p.add_argument('--request', help='a dump request to send, as hex bytes')
    p.add_argument('--wait', type=float, default=30.0)
    p.add_argument('--quiet', type=float, default=1.0, help='end of dump after S s without data')
    p = sub.add_parser('kit-roundtrip')
    p.add_argument('dir')
    p.add_argument('--keys', help='key sequence that starts SYSEX SEND of the kit')
    p.add_argument('--request', help='or a dump request, as hex bytes')
    p.add_argument('--receive-keys', help='key sequence that readies the unit to receive, if needed')
    p.add_argument('--settle', type=float, default=2.0, help='s to wait after sending the kit back')
    p = sub.add_parser('data')
    p.add_argument('op', choices=('ls', 'get', 'put', 'roundtrip'))
    p.add_argument('path')
    p.add_argument('file', nargs='?')
    p.add_argument('--to', help='roundtrip: write to this path instead of PATH')
    p.add_argument('--yes', action='store_true', help='put/roundtrip: allow replacing the object')
    args = ap.parse_args(argv)

    if args.cmd == 'data' and args.op in ('put', 'roundtrip') and not args.yes:
        ap.error('data %s replaces the object on the unit: add --yes' % args.op)
    if args.cmd == 'data' and args.op != 'ls' and not args.file:
        ap.error('data %s needs a file or directory' % args.op)

    port = open_port()
    s = Session(port, getattr(args, 'out', '.'))
    try:
        if args.cmd == 'shot':
            screen = s.shot(args.out, args.scale)
            print('%s: %dx%d, %d lit pixels' % (args.out, screen.w, screen.h, screen.lit()))
        elif args.cmd in ('run', 'do'):
            os.makedirs(args.out, exist_ok=True)
            if args.cmd == 'do':
                text = args.steps
            elif args.script == '-':
                text = sys.stdin.read()
            else:
                with open(args.script) as fh:
                    text = fh.read()
            s.run(text)
            print('done')
        elif args.cmd == 'info':
            pid, name = s.rpc.ping()
            print('%s (product id 0x%02X), OS %s' % (name, pid, s.rpc.version()))
        elif args.cmd == 'dump-capture':
            msgs = s.capture(args.keys, bytes.fromhex(args.request) if args.request else None,
                             args.wait, args.quiet)
            save_syx(args.out, msgs)
            print('%s: %d messages, %d bytes' % (args.out, len(msgs), sum(map(len, msgs))))
        elif args.cmd == 'kit-roundtrip':
            os.makedirs(args.dir, exist_ok=True)
            req = bytes.fromhex(args.request) if args.request else None
            first = s.capture(args.keys, req)
            save_syx(os.path.join(args.dir, 'kit-out.syx'), first)
            print('received %d messages, %d bytes' % (len(first), sum(map(len, first))))
            if args.receive_keys:
                s.key_steps(args.receive_keys)
            s.send_dump(first)
            time.sleep(args.settle)
            s.shot(os.path.join(args.dir, 'after-receive.png'))
            again = s.capture(args.keys, req)
            save_syx(os.path.join(args.dir, 'kit-back.syx'), again)
            diffs = compare(first, again)
            if diffs:
                print('FAIL: the kit came back different: %s%s' % (
                    diffs[:12], ' ...' if len(diffs) > 12 else ''))
                return 1
            print('PASS: sent back and dumped again, byte-identical')
        elif args.cmd == 'data':
            if args.op == 'ls':
                for e in s.rpc.data_list(args.path):
                    if 'id' in e:
                        print('%-24s id %4d  %8d B  ops 0x%04x%s%s%s' % (
                            e['name'] or '-', e['id'], e['size'], e['ops'], '  dir' if e['dir'] else '',
                            '' if e['valid'] else '  (no valid data)', '  +metadata' if e['metadata'] else ''))
                    else:
                        print('%-24s dir, %d entries' % (e['name'], e['entries']))
            elif args.op == 'get':
                data = s.rpc.data_read(args.path, progress=progress)
                with open(args.file, 'wb') as fh:
                    fh.write(data)
                print('\n%s: %d bytes' % (args.file, len(data)))
            elif args.op == 'put':
                with open(args.file, 'rb') as fh:
                    data = fh.read()
                backup(s, args.path, args.file + '.before.bin')
                s.rpc.data_write(args.path, data, progress=progress)
                print('\nwrote %d bytes to %s' % (len(data), args.path))
            else:
                os.makedirs(args.file, exist_ok=True)
                to = args.to or args.path
                data = s.rpc.data_read(args.path, progress=progress)
                save(os.path.join(args.file, 'out.bin'), data)
                print('\nread %s: %d bytes' % (args.path, len(data)))
                if to != args.path:
                    backup(s, to, os.path.join(args.file, 'target-before.bin'))
                s.rpc.data_write(to, data, progress=progress)
                print('\nwrote %s' % to)
                back = s.rpc.data_read(to, progress=progress)
                save(os.path.join(args.file, 'back.bin'), back)
                if back != data:
                    n = sum(x != y for x, y in zip(back, data)) + abs(len(back) - len(data))
                    print('\nFAIL: %s read back %d bytes, %d differ' % (to, len(back), n))
                    return 1
                print('\nPASS: %s read back byte-identical (%d bytes)' % (to, len(back)))
        return 0
    finally:
        try:
            s.close()
        finally:
            port.close()


if __name__ == '__main__':
    sys.exit(main())
