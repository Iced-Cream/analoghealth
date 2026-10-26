# SPDX-License-Identifier: GPL-2.0-or-later
"""One program on a unit's USB MIDI port: keys, screenshots, notes and CCs,
SysEx dumps and the data store, in one session, so a step can look at the
screen while a key is held. The Analog Rytm MKII by default; --port NAME
(before the command) picks another unit, e.g. --port "Analog Four", or
--port "IN,OUT" names the input and output ports apart (an emulator's
loopback ports: --port "a4-emu usb out,a4-emu usb in"). --din PORT, in the
same forms, names the MIDI interface cabled to the unit's DIN ports (or an
emulator's DIN MIDI over TCP) for send and capture with a trailing din. The
kit and pattern dump commands work on both units: the unit's product id (RPC
Ping) picks its SysEx device byte.

    python analogrun.py shot OUT.png [--scale N]       the screen now (stock OS too)
    python analogrun.py run SCRIPT [--out DIR]         steps from a file ('-' = stdin)
    python analogrun.py [--quiet] run PLAN             a test plan, one verdict (testplan.py)
    python analogrun.py --quiet [--log F] COMMAND ...  one line: ok, or what failed
    python analogrun.py do 'hold FUNC; shot f.png; release FUNC'
    python analogrun.py dump-capture OUT.syx [--kit N | --work-kit | --work-pattern | --keys SEQ | --request HEX] [--quiet S]
    python analogrun.py kit-roundtrip DIR --kit N      dump kit N, send it back, dump again
    python analogrun.py info                           product, OS version
    python analogrun.py active                         the active project's kit 1 name
    python analogrun.py data ls PATH
    python analogrun.py data get PATH OUT
    python analogrun.py data put PATH IN [--name NAME] --yes   replaces an item
    python analogrun.py data roundtrip PATH DIR [--to PATH2] [--name NAME] --yes
    python analogrun.py data clear PATH BACKUP --yes   empties an item (saves it first)

Steps (one per line or separated by ';'; '#' starts a comment):

    tap KEY [MS]          press and release (analoghealth build)
    hold KEY [MS]         press; the firmware lets go after MS (default its maximum, 2 s);
                          key revision 5 holds up to 4 keys at once, each with its own MS
    release KEY
    keys SEQ              as analogusb.py keys: 'GLOBAL,DOWN,FUNC(YES)'; with key revision 5,
                          several holds: 'T5+FUNC(RECORD)' (pressed in order, let go in reverse)
    turn ENC DELTA [STEP] encoder turn, if the analoghealth build has them
    pad PADS [VEL] [HOLD_MS] [raw=R] [pressure=V,V,..] [every=MS] [key=bridge|panel|none]
                          Rytm pads struck as the panel strikes them (analoghealth 1.3):
                          PADS one name or several (BD,SD), VEL 1-127 (default 100) or the
                          raw force R, held HOLD_MS (default 100), pressure values every MS
    padplay FILE.json [pads=BD,SD]  a pad performance logged with analogusb.py padcap, again
    shot FILE             screenshot: .png (4x) or .bin (raw framebuffer)
    expect-screen NAME [S]  stop unless the screen equals NAME.bin (in the plan's refs
                          folder), retrying for S seconds; a mismatch reports the differing
                          rows and columns and saves NAME-got.png and NAME-diff.png;
                          rows=A-B (anywhere after NAME) compares rows A..B only;
                          settle: a match must still hold 0.4 s later
    cc CH NUM VAL         MIDI channel 1-16
    nrpn CH MSB LSB VAL   VAL 0-16383 (CC 99, 98, 6, 38)
    note CH NOTE VEL [MS] note on, off after MS (default 100)
    wait S
    stats                 one analoghealth CPU/DSP/heap line
    dump ADDR LEN FILE    DDR to a file (analoghealth)
    send FILE.syx [din]   send the SysEx messages in a file (e.g. a saved dump); din: into
                          the unit's DIN MIDI input (--din), the write policy still asked
                          over USB
    capture FILE.syx S [any|none] [din]  listen S seconds, then save every SysEx the unit sent
                          since the last send (or since this step) to FILE, the session's
                          own RPC and diagnostics traffic left out; FILE is written even
                          when nothing came. any: stop unless something came; none: stop
                          if anything came. din: what came out of its DIN MIDI output
                          since the last send there, not USB
    ping                  the unit's product, name and OS version (RPC Ping, read-only)
    save-project N NAME SEQ  key-driven SAVE PROJECT into slot N (keys SEQ, ending on the
                          save), checked, then renamed NAME; slot N must be empty (a
                          write policy's before_save_project may empty it)
    save-kit SEQ          key-driven kit save (keys SEQ), checked
    say TEXT              print TEXT
    listen TEXT           the person listens for TEXT: the run sends nothing until they
                          have finished (Enter; or, with ANALOGRUN_LISTEN_FILE set, until
                          the host removes that file); skipped in emulator plans

Kit dumps: the OS answers a dump request

    F0 00 20 3C <dev> 00 <id> 01 01 <number> 00 00 00 05 F7

(<dev> 07 on the Analog Rytm MKII, 06 on the Analog Four MKII) with the
object as a dump (<id> - 0x10): 62 a saved kit 0-127 (--kit N), 68 the kit
being edited (--work-kit), 6A the pattern being edited (--work-pattern); the
number is not used for the last two. Both units store a kit dump they receive
in the slot the dump names at any time, and reload it when that slot is the
active kit, so a round trip needs no menu on the unit. A kit's name is bytes
4-19 of the unpacked dump on both.

A local write policy (writepolicy.py), if one is installed on this computer,
is asked before every write, rename, clear, dump sent and key-driven save, and
can refuse it (exit 2). Without one the runner writes wherever it is told.

Key steps open a analoghealth key session on first use and close it (releasing
any synthetic key) at the end; everything else works on a stock OS. Each
command here is the only user of the port while it runs: close Transfer,
Overbridge's MIDI, screendump and analogusb.py first.
"""
import argparse
import json
import os
import shlex
import struct
import sys
import time

import elkrpc
import testplan
import unitdata
import writepolicy
from analogusb import Diag, RemoteKeys, decode_stats, key_sequence, peek, play_sequence, req_stats, pad_caps
import analogpads

ELEKTRON = bytes([0xF0, 0x00, 0x20, 0x3C])
KIT_REQUEST, WORK_KIT_REQUEST, WORK_PATTERN_REQUEST = 0x62, 0x68, 0x6A
RYTM, FOUR = 0x07, 0x06
# RPC Ping product id -> dump device byte. By id, not name: the A4 MKII OS
# answers "Analog Four" on a unit but "Analog Keys" in the emulator.
DUMP_DEVICES = {0x10: RYTM, 0x0E: FOUR}       # Analog Rytm MKII, Analog Four MKII


def dump_device(pid, name=''):
    """The SysEx device byte of a unit's dumps, from its RPC Ping reply."""
    if pid not in DUMP_DEVICES:
        raise ValueError('no kit/pattern dumps known for %s (product id 0x%02X): Analog Rytm '
                         'MKII or Analog Four MKII only' % (name or 'this unit', pid))
    return DUMP_DEVICES[pid]


def dump_request(kind, number=0, device=RYTM):
    """A dump request: the object comes back as dump id kind - 0x10."""
    if not 0 <= number < 128:
        raise ValueError('number 0..127')
    return bytes([0xF0, 0x00, 0x20, 0x3C, device, 0x00, kind, 0x01, 0x01, number,
                  0x00, 0x00, 0x00, 0x05, 0xF7])


class Tap:
    """The Port, keeping every SysEx that arrives so a dump sent while another
    request waits for its reply is not lost."""

    def __init__(self, port):
        self.port = port
        self.seen = []
        self.since = None                # a list while a capture step is listening

    def send(self, data):
        self.port.send(data)

    def send_short(self, *data):
        self.port.send_short(*data)

    def receive(self, seconds=1.0, until=None):
        msgs = self.port.receive(seconds, until)
        self.seen.extend(msgs)
        if self.since is not None:
            self.since.extend(msgs)
        return msgs


OUT = None                       # --quiet points this at a log (None: stdout)


def say(*args, **kw):
    kw.setdefault('flush', True)
    print(*args, file=OUT or sys.stdout, **kw)


def screen_diff(got, want, w=128, h=64, rows=None):
    """Two framebuffers -> (differing pixels as (y, x), a short text report); `rows`
    (first, last) compares only those rows."""
    g, r = elkrpc.Screen(w, h, got).rows(), elkrpc.Screen(w, h, want).rows()
    first, last = rows or (0, h - 1)
    diff = [(y, x) for y in range(first, last + 1) for x in range(w) if g[y][x] != r[y][x]]
    if not diff:
        return diff, 'identical'
    ys, xs = [d[0] for d in diff], [d[1] for d in diff]
    y0, y1, x0, x1 = min(ys), max(ys), min(xs), max(xs)
    text = '%d pixels differ in rows %d-%d, columns %d-%d%s' % (
        len(diff), y0, y1, x0, x1, ' (comparing rows %d-%d)' % rows if rows else '')
    if y1 - y0 < 12 and x1 - x0 < 64:          # small enough to show: + lit only now, - only before
        art = []
        for y in range(y0, y1 + 1):
            art.append(''.join('+' if g[y][x] and not r[y][x] else '-' if r[y][x] and not g[y][x]
                               else '#' if g[y][x] else '.' for x in range(x0, x1 + 1)))
        text += '\n    ' + '\n    '.join(art)
    return diff, text


def ours(m):
    """The session's own traffic: RPC (elkrpc) or analoghealth's diagnostics channel."""
    return m.startswith(elkrpc.HEADER) or (m.startswith(ELEKTRON) and m[4:6] == b'\x7d\x00')


def is_dump(m):
    """Elektron SysEx that is neither the RPC channel nor analoghealth's."""
    return m.startswith(ELEKTRON) and not m.startswith(elkrpc.HEADER) and m[4:6] != b'\x7d\x00'


class Session:
    def __init__(self, port, out='.', din=None):
        self.port = Tap(port)
        self.din = Tap(din) if din is not None else None     # the DIN MIDI port (--din)
        self.rpc = elkrpc.Rpc(self.port)
        self.diag = Diag(self.port)
        self.out = out
        self.refs = None                 # folder of reference screens (.bin)
        self.record_refs = False         # expect-screen saves the screen as the reference
        self._keys = None
        self._device = None

    def dump_request(self, kind, number=0):
        """A dump request in this unit's SysEx (its device byte from RPC Ping, once)."""
        if self._device is None:
            self._device = dump_device(*self.rpc.ping())
        return dump_request(kind, number, self._device)

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

    def leg(self, a, least):
        """A send/capture step's words -> (its Tap, the words without a trailing din)."""
        if len(a) > least and a[-1].lower() == 'din':
            if self.din is None:
                raise ValueError('din: no DIN MIDI port (give --din PORT)')
            return self.din, a[:-1]
        return self.port, a

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
        play_sequence(self.keys, key_sequence(text))

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
                raise ValueError('this analogusb.py has no encoder turns')
            self.keys.turn(a[0].upper(), num(a[1]), num(a[2]) if len(a) > 2 else 1)
        elif op == 'pad':
            opts = dict(w.split('=', 1) for w in a[1:] if '=' in w)
            a = [w for w in a if '=' not in w]
            if set(opts) - {'raw', 'pressure', 'every', 'key'} or not 1 <= len(a) <= 3:
                raise ValueError('pad PADS [VEL] [HOLD_MS] [raw=R] [pressure=V,..] [every=MS] [key=..]')
            threshold = pad_caps(self.diag)['threshold']
            raw = (num(opts['raw']) if 'raw' in opts
                   else analogpads.raw_for(num(a[1]) if len(a) > 1 else 100, threshold))
            key = opts.get('key', 'bridge')
            if key not in ('bridge', 'panel', 'none'):
                raise ValueError('key=bridge, panel or none')
            pressure = [num(v) for v in opts['pressure'].split(',')] if 'pressure' in opts else []
            for name in a[0].upper().split(','):
                self.keys.pads(analogpads.hit(name, raw, num(a[2]) if len(a) > 2 else 100, pressure,
                                              num(opts.get('every', '10')),
                                              None if key == 'none' else key))
                say('  %s struck: raw %d, velocity %d' % (name, raw, analogpads.velocity(raw, threshold)))
        elif op == 'padplay':
            which = [w[5:].upper().split(',') for w in a[1:] if w.startswith('pads=')]
            entries, _ = analogpads.load(self.path(a[0]))
            chained = pad_caps(self.diag)['queue'] > 0       # PAD NEXT: the take's own timing
            plan = analogpads.replay_plan(analogpads.messages(entries), which[0] if which else None,
                                          carry=chained)
            if not plan:
                raise ValueError('%s holds no pad messages' % a[0])
            if chained:
                self.keys.pad_chain(plan)
            else:
                for _, script in plan:
                    self.keys.pads(script)
            say('  played %d pad events' % sum(len(s) for _, s in plan))
        elif op == 'shot':
            s = self.shot(a[0])
            say('  %s: %dx%d, %d lit' % (a[0], s.w, s.h, s.lit()))
        elif op == 'expect-screen':
            # expect-screen NAME[.bin] [WAIT_S] [rows=A-B]: compare with the reference (refs
            # folder if set), retrying for up to WAIT_S while the screen settles; rows=A-B
            # compares only rows A..B (a test build's own drawing elsewhere, say, that
            # depends on timing), and the failure report and diff picture cover only those.
            # `settle` (testplan adds it when it folds a wait into the check): a match counts
            # only when the screen still matches 0.4 s later.
            settle = 'settle' in a[1:]
            a = [w for w in a if w != 'settle']
            opts = [w for w in a[1:] if w.startswith('rows=')]
            a = a[:1] + [w for w in a[1:] if not w.startswith('rows=')]
            span = None
            if opts:
                try:
                    span = tuple(int(v) for v in opts[0][5:].split('-'))
                    assert len(span) == 2 and 0 <= span[0] <= span[1] <= 63
                except (ValueError, AssertionError):
                    raise ValueError('rows=A-B with 0 <= A <= B <= 63, not %r' % opts[0])
            ref = a[0] if a[0].endswith('.bin') else a[0] + '.bin'
            refpath = os.path.join(self.refs, ref) if self.refs and not os.path.isabs(ref) else self.path(ref)
            got = self.rpc.screenshot()
            if self.record_refs:
                os.makedirs(os.path.dirname(refpath) or '.', exist_ok=True)
                with open(refpath, 'wb') as fh:
                    fh.write(got.pixels)
                with open(os.path.splitext(refpath)[0] + '.png', 'wb') as fh:
                    fh.write(got.png(4))
                say('  recorded %s' % refpath)
                return
            with open(refpath, 'rb') as fh:
                want = fh.read()
            end = time.monotonic() + (float(a[1]) if len(a) > 1 else 0)

            def same(g):
                return g.pixels == want if span is None else not screen_diff(
                    g.pixels, want, g.w, g.h, span)[0]
            while True:
                while not same(got) and time.monotonic() < end:
                    time.sleep(0.2)
                    got = self.rpc.screenshot()
                if not settle or not same(got):
                    break
                time.sleep(0.4)
                got = self.rpc.screenshot()
                if same(got) or time.monotonic() >= end:
                    break
            if not same(got):
                diff, text = screen_diff(got.pixels, want, got.w, got.h, span)
                stem = self.path(os.path.splitext(os.path.basename(ref))[0])
                with open(stem + '-got.png', 'wb') as fh:
                    fh.write(got.png(4))
                with open(stem + '-diff.png', 'wb') as fh:
                    fh.write(diff_png(got.w, got.h, diff))
                raise AssertionError('screen differs from %s: %s (saved %s-got.png, -diff.png)'
                                     % (ref, text, os.path.basename(stem)))
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
            say('  second %d: CPU %.1f %%, DSP %.1f %% (peak %.1f %%), heap free %d' % (
                s['seconds'], 100 * s.get('cpu', 0), 100 * s.get('dsp', 0),
                100 * s.get('dsp_peak', 0), s['heap_free']))
        elif op == 'dump':
            data = peek(self.diag, num(a[0]), num(a[1]))
            with open(self.path(a[2]), 'wb') as fh:
                fh.write(data)
        elif op == 'send':
            tap, a = self.leg(a, 1)
            msgs = load_syx(self.path(a[0]))
            writepolicy.before_dumps(self, msgs)     # over USB RPC, whichever port sends
            tap.since = []                   # a capture step after this keeps the replies
            self.send_dump(msgs, port=tap)
            say('  sent %d messages, %d bytes%s' % (len(msgs), sum(map(len, msgs)),
                                                    ' over DIN' if tap is self.din else ''))
        elif op == 'capture':
            # capture FILE S [any|none] [din]: the replies to a send, or the silence, as evidence
            tap, a = self.leg(a, 2)
            want = a[2].lower() if len(a) > 2 else None
            if want not in (None, 'any', 'none') or len(a) > 3:
                raise ValueError('capture FILE S [any|none] [din]')
            if tap.since is None:
                tap.since = []
            end = time.monotonic() + float(a[1])
            while time.monotonic() < end:
                tap.receive(min(0.05, max(end - time.monotonic(), 0)))
            got = [m for m in tap.since if m[:1] == b'\xF0' and not ours(m)]
            tap.since = []                   # the next capture keeps only what comes after
            save_syx(self.path(a[0]), got)
            say('  captured %d messages, %d bytes' % (len(got), sum(map(len, got))))
            if want == 'any' and not got:
                raise AssertionError('no SysEx came back within %s s' % a[1])
            if want == 'none' and got:
                raise AssertionError('%d SysEx message(s) came back (saved in %s)'
                                     % (len(got), os.path.basename(a[0])))
        elif op == 'ping':
            pid, name = self.rpc.ping()
            say('  ping: product 0x%02X %r, OS %s' % (pid, name, self.rpc.version()))
        elif op == 'save-project':
            # save-project N NAME SEQ: SEQ ends on the unit's save (its naming screen's
            # YES); the new project is then renamed NAME over the data store.
            path = '/projects/%d' % num(a[0])
            name = writepolicy.check_write(self.rpc, path, a[1])
            if unitdata.target(self.rpc, path)[0] is not None:
                note = writepolicy.before_save_project(self, path)
                if note:
                    say('  ' + note)
            held, _ = unitdata.target(self.rpc, path)
            if held is not None:
                # The A4's SAVE PROJECT AS asks OVERWRITE '<name>'? first when the slot
                # holds a project other than the active one, so SEQ
                # may end on the naming screen, and a full slot would not show the save.
                raise unitdata.DataError('%s holds %r: save-project needs an empty slot '
                                         '(data clear it first)' % (path, held))
            self.key_steps(' '.join(a[2:]))
            end = time.monotonic() + 15
            while unitdata.target(self.rpc, path)[0] is None:
                if time.monotonic() > end:
                    raise unitdata.DataError('%s still empty 15 s after the keys: check the unit'
                                             % path)
                time.sleep(0.5)
            mark(self, path, name)
            say('  %s saved, named %r' % (path, name))
            # SAVE PROJECT AS makes the saved copy the active project (seen on the unit).
            note = writepolicy.after_save_project(self, path, name)
            if note:
                say('  ' + note)
        elif op == 'save-kit':
            note = writepolicy.before_save_kit(self)
            if note:
                say('  ' + note)
            self.key_steps(' '.join(a))
        elif op == 'say':
            say(' '.join(a))
        elif op == 'listen':
            # The person listens to the unit: nothing more is sent until they say they have
            # finished, however long that takes.
            self.close()                     # no synthetic key stays held meanwhile
            what = ' '.join(a)
            say('  listening: %s' % what)
            flag = os.environ.get('ANALOGRUN_LISTEN_FILE')
            if flag:
                # A host that asks the person itself: it removes the file when they have finished.
                with open(flag, 'w', encoding='utf-8') as fh:
                    json.dump({'what': what, 'since': time.time()}, fh)
                while os.path.exists(flag):
                    time.sleep(0.5)
            else:
                input('  press Enter when you have finished listening ')
            say('  finished listening')
        else:
            raise ValueError('unknown step %r' % op)

    def run(self, text):
        lines = [s for line in text.splitlines() for s in line.split(';')]
        for n, line in enumerate(lines, 1):
            if line.strip() and not line.strip().startswith('#'):
                say('%3d  %s' % (n, line.strip()))
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
            say('start the SysEx send on the unit now (waiting %g s)' % wait)
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

    def send_dump(self, msgs, gap=0.05, port=None):
        for m in msgs:
            (port or self.port).send(m)
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
        say('saved the current %s to %s' % (path, out))
    except elkrpc.RpcError:
        say('%s holds nothing to back up' % path)


def mark(s, path, name):
    """Give a just-written item its name and confirm it in the listing; no name
    (none asked for or given by the policy): leave it as written."""
    if name is None:
        return
    unitdata.rename(s.rpc, path, name)
    now, _ = unitdata.target(s.rpc, path)
    if now != name:
        raise unitdata.DataError('%s should be named %r but lists as %r' % (path, name, now))


def diff_png(w, h, diff):
    """A PNG with only the differing pixels lit."""
    stride = (h + 31) // 32
    cols = [0] * w
    for y, x in diff:
        cols[x] |= 1 << (stride * 32 - h + y)
    pixels = b''.join(c.to_bytes(stride * 4, 'big') for c in cols)
    return elkrpc.Screen(w, h, pixels).png(4)


def open_port(name='Analog Rytm'):
    if sys.platform == 'win32':
        from winmidi import Port
    else:
        from midimido import Port
    return Port(name)


def progress(frac):
    say('\r  %5.1f %%' % (100 * frac), end='', flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser(prog='analogrun.py', description=__doc__.split('\n')[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__.split('\n', 1)[1])
    ap.add_argument('--port', default=None,
                    help='part of the unit\'s MIDI port name (default: Analog Rytm), or IN,OUT')
    ap.add_argument('--din', default=None,
                    help='the MIDI port cabled to the unit\'s DIN ports (name, IN,OUT or tcp:ADDR), '
                         'for send/capture ... din')
    ap.add_argument('-q', '--quiet', dest='quiet_mode', action='store_true',
                    help='print one line (ok, or what failed); the rest goes to --log')
    ap.add_argument('--log', help='with --quiet: append the full output to this file')
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
    sub.add_parser('active', help="the active project's kit 1 name, and whether it is a test project")
    p = sub.add_parser('dump-capture')
    p.add_argument('out')
    p.add_argument('--kit', type=int, help='request saved kit N (0-127, kit 1 on the unit is 0)')
    p.add_argument('--work-kit', action='store_true', help='request the kit being edited')
    p.add_argument('--work-pattern', action='store_true', help='request the pattern being edited')
    p.add_argument('--keys', help='or a key sequence that starts SYSEX SEND on the unit')
    p.add_argument('--request', help='or any request, as hex bytes')
    p.add_argument('--wait', type=float, default=30.0)
    p.add_argument('--quiet', type=float, default=1.0, help='end of dump after S s without data')
    p = sub.add_parser('kit-roundtrip')
    p.add_argument('dir')
    p.add_argument('--kit', type=int, help='saved kit N (0-127, kit 1 on the unit is 0)')
    p.add_argument('--keys', help='or a key sequence that starts SYSEX SEND of the kit')
    p.add_argument('--request', help='or a dump request, as hex bytes')
    p.add_argument('--receive-keys', help='key sequence that readies the unit to receive, if needed')
    p.add_argument('--settle', type=float, default=2.0, help='s to wait after sending the kit back')
    p = sub.add_parser('data')
    p.add_argument('op', choices=('ls', 'get', 'put', 'roundtrip', 'clear'))
    p.add_argument('path')
    p.add_argument('file', nargs='?')
    p.add_argument('--to', help='roundtrip: write to this path instead of PATH')
    p.add_argument('--name', help="the target's new name")
    p.add_argument('--yes', action='store_true', help='put/roundtrip/clear: allow replacing or emptying the object')
    args = ap.parse_args(argv)

    if args.cmd == 'run' and args.script != '-' and testplan.is_plan(args.script):
        return testplan.run_plan(args.script, port=args.port, quiet=args.quiet_mode, din=args.din)
    if not args.quiet_mode:
        return _main(ap, args)
    global OUT
    OUT = open(args.log, 'a', encoding='utf-8') if args.log else open(os.devnull, 'w')
    try:
        rc = _main(ap, args)
    except Exception as e:                       # noqa: BLE001 - one line, details in the log
        import traceback
        traceback.print_exc(file=OUT)
        print('failed: %s: %s' % (type(e).__name__, e))
        return 1
    finally:
        OUT.close()
        OUT = None
    print('ok' if rc == 0 else 'failed (exit %d)%s' % (rc, ', see ' + args.log if args.log else ''))
    return rc


def _main(ap, args):
    if args.cmd == 'data' and args.op in ('put', 'roundtrip', 'clear') and not args.yes:
        ap.error('data %s replaces the object on the unit: add --yes' % args.op)
    if args.cmd == 'data' and args.op != 'ls' and not args.file:
        ap.error('data %s needs a file or directory' % args.op)

    want = None                         # (dump request id, number) once the unit is known
    request = None
    if args.cmd in ('dump-capture', 'kit-roundtrip'):
        if getattr(args, 'work_kit', False):
            want = (WORK_KIT_REQUEST, 0)
        elif getattr(args, 'work_pattern', False):
            want = (WORK_PATTERN_REQUEST, 0)
        elif args.kit is not None:
            if not 0 <= args.kit < 128:
                ap.error('--kit is 0..127')
            want = (KIT_REQUEST, args.kit)
        elif args.request:
            request = bytes.fromhex(args.request)
        if args.cmd == 'kit-roundtrip' and not (want or request or args.keys):
            ap.error('kit-roundtrip needs --kit N (or --keys / --request)')

    port = open_port(args.port or 'Analog Rytm')
    try:
        din = open_port(args.din) if args.din else None
    except BaseException:
        port.close()
        raise
    # --out is a folder for run and do; for shot and dump-capture it is the file itself
    s = Session(port, args.out if args.cmd in ('run', 'do') else '.', din)
    try:
        if want:
            request = s.dump_request(*want)
        if args.cmd == 'shot':
            screen = s.shot(args.out, args.scale)
            say('%s: %dx%d, %d lit pixels' % (args.out, screen.w, screen.h, screen.lit()))
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
            say('done')
        elif args.cmd == 'info':
            pid, name = s.rpc.ping()
            say('%s (product id 0x%02X), OS %s' % (name, pid, s.rpc.version()))
        elif args.cmd == 'active':
            msgs = s.capture(request=s.dump_request(KIT_REQUEST, 0), wait=5.0, quiet=0.5)
            name = unitdata.kit_name(msgs[0])
            note = writepolicy.describe_active(name)
            say('active project: kit 1 is %r%s' % (name, ' (%s)' % note if note else ''))
        elif args.cmd == 'dump-capture':
            msgs = s.capture(args.keys, request, args.wait, args.quiet)
            save_syx(args.out, msgs)
            say('%s: %d messages, %d bytes' % (args.out, len(msgs), sum(map(len, msgs))))
        elif args.cmd == 'kit-roundtrip':
            os.makedirs(args.dir, exist_ok=True)
            req = request
            first = s.capture(args.keys, req, wait=5.0 if req else 30.0)
            save_syx(os.path.join(args.dir, 'kit-out.syx'), first)
            say('received %d messages, %d bytes' % (len(first), sum(map(len, first))))
            writepolicy.before_dumps(s, first)
            if args.receive_keys:
                s.key_steps(args.receive_keys)
            s.send_dump(first)
            time.sleep(args.settle)
            s.shot(os.path.join(args.dir, 'after-receive.png'))
            again = s.capture(args.keys, req, wait=5.0 if req else 30.0)
            save_syx(os.path.join(args.dir, 'kit-back.syx'), again)
            diffs = compare(first, again)
            if diffs:
                say('FAIL: the kit came back different: %s%s' % (
                    diffs[:12], ' ...' if len(diffs) > 12 else ''))
                return 1
            say('PASS: sent back and dumped again, byte-identical')
        elif args.cmd == 'data':
            if args.op == 'ls':
                for e in s.rpc.data_list(args.path):
                    if 'id' in e:
                        say('%-24s id %4d  %8d B  ops 0x%04x%s%s%s' % (
                            e['name'] or '-', e['id'], e['size'], e['ops'], '  dir' if e['dir'] else '',
                            '' if e['valid'] else '  (no valid data)', '  +metadata' if e['metadata'] else ''))
                    else:
                        say('%-24s dir, %d entries' % (e['name'], e['entries']))
            elif args.op == 'get':
                data = s.rpc.data_read(args.path, progress=progress)
                with open(args.file, 'wb') as fh:
                    fh.write(data)
                say('\n%s: %d bytes' % (args.file, len(data)))
            elif args.op == 'clear':
                name, _ = unitdata.target(s.rpc, args.path)
                if name is None:
                    say('%s is already empty' % args.path)
                    return 0
                writepolicy.check_write(s.rpc, args.path)
                backup(s, args.path, args.file)
                s.rpc.data_clear(args.path)
                say('cleared %s' % args.path)
            elif args.op == 'put':
                with open(args.file, 'rb') as fh:
                    data = fh.read()
                name = writepolicy.check_write(s.rpc, args.path, args.name)
                backup(s, args.path, args.file + '.before.bin')
                s.rpc.data_write(args.path, data, progress=progress)
                mark(s, args.path, name)
                say('\nwrote %d bytes to %s, named %r' % (len(data), args.path, name))
            else:
                os.makedirs(args.file, exist_ok=True)
                to = args.to or args.path
                data = s.rpc.data_read(args.path, progress=progress)
                save(os.path.join(args.file, 'out.bin'), data)
                say('\nread %s: %d bytes' % (args.path, len(data)))
                name = writepolicy.check_write(s.rpc, to, args.name)
                if to != args.path:
                    backup(s, to, os.path.join(args.file, 'target-before.bin'))
                s.rpc.data_write(to, data, progress=progress)
                # Read back before the rename: renaming rewrites the stored image.
                try:
                    back = s.rpc.data_read(to, progress=progress)
                finally:
                    mark(s, to, name)
                say('\nwrote %s, named %r' % (to, name))
                save(os.path.join(args.file, 'back.bin'), back)
                expected = {0x17, 0x18}         # the slot number (u16), which the unit rewrites
                if len(back) != len(data) or any(
                        x != y for i, (x, y) in enumerate(zip(data, back)) if i not in expected):
                    n = sum(x != y for x, y in zip(back, data)) + abs(len(back) - len(data))
                    at = [i for i, (x, y) in enumerate(zip(data, back)) if x != y][:8]
                    say('\nFAIL: %s read back %d bytes, %d differ: %s' % (to, len(back), n, ', '.join(
                        '0x%x %02x->%02x' % (i, data[i], back[i]) for i in at)))
                    return 1
                say('\nPASS: %s read back identical (%d bytes) apart from its slot number, '
                      'then named %r' % (to, len(back), name))
        return 0
    except writepolicy.Refused as e:
        say('\nrefused (write policy): %s' % e)
        return 2
    except unitdata.DataError as e:
        say('\nrefused: %s' % e)
        return 2
    finally:
        try:
            s.close()
        finally:
            port.close()
            if din is not None:
                din.close()


if __name__ == '__main__':
    sys.exit(main())
