# SPDX-License-Identifier: GPL-2.0-or-later
"""Read analoghealth diagnostics or operate bounded keys and encoders over USB MIDI,
on the Analog Rytm MKII (OS 1.74) and the Analog Four MKII (OS 1.56).

    python analogusb.py cfw                  which mod answers, and its uptime
    python analogusb.py stats [N]            CPU, DSP, clock, heap: N readings, one a second
    python analogusb.py peek ADDR [LEN]      hex dump of DDR or the SRAM (0x8000xxxx)
    python analogusb.py dump ADDR LEN OUT    DDR to a file
    python analogusb.py decode FILE          decode the replies in a file of raw MIDI bytes
    python analogusb.py watch [S] [--until KEY]  print panel keys as they go down and up
    python analogusb.py selftest             check the encoder and decoder, no device
    python analogusb.py keys YES
    python analogusb.py keys 'FUNC(PLAY)'
    python analogusb.py keys 'GLOBAL,DOWN,DOWN,YES'
    python analogusb.py turn E -12 [--step N] [--hold FUNC]   encoder E 12 counts left
    python analogusb.py pad BD [--vel 100 | --raw R] [--hold MS] [--pressure 40,80 --every MS]
    python analogusb.py padcap [S] [--out FILE.json]   log what the Rytm's panel sends for S s
    python analogusb.py padplay FILE.json [--pads BD,SD] [--log OUT.json]   send a logged pad performance again
    python analogusb.py --port "Analog Four" ...   pick the unit when both are connected

irpina's digiusb.py (digihealth) for the Rytm and the A4. analoghealth answers
the same SysEx channel: Elektron's manufacturer header
with a device byte (0x7D) no Elektron machine uses, so a stock OS drops the
messages and talking to it does nothing. Diagnostics only read. The optional
key helper supports bounded individual key actions after capability detection.

    request  F0 00 20 3C 7D 00 <pack7: u16 seq, u8 cmd, payload> F7
    reply    F0 00 20 3C 7D 00 <pack7: u16 seq, u8 cmd | 0x80, u8 status,
                                payload> F7

Integers are big-endian. status 0 is ok, 1 an argument the mod refuses, 2 an
unknown command. Windows uses winmidi.py (ctypes: no packages); macOS/Linux
use Mido + python-rtmidi (midimido.py).
Windows lets one program at a time open a MIDI port: close Elektron
Transfer first.
"""
import argparse
import re
import secrets
import struct
import sys
import time
from keymap import KEYSETS, MAPS, SUPPORTED, PRODUCTS, ENCODERS, ALL_KEYS, ALL_ENCODERS, A4
import analogpads as pads

HEADER = bytes([0xF0, 0x00, 0x20, 0x3C, 0x7D, 0x00])
HELLO, STATS, PEEK = 0x01, 0x02, 0x03
STATUS = {0: 'ok', 1: 'refused', 2: 'unknown command', 3: 'busy',
          4: 'stale session or operation', 5: 'physical overlap',
          6: 'remote keys are off (SETTINGS > REMOTE KEYS)'}
CAPS, OPEN, ACTION, KEY_STATUS, CANCEL = range(0x10, 0x15)
PRESS, RELEASE, TAP, TURN = 1, 2, 3, 4
MAX_TURNS, MAX_STEP = 30, 30            # per TURN action: UI ticks (20 a second), counts per tick
KEY_STATES = ('none', 'accepted', 'completed', 'cancelled', 'expired', 'failed')
PEEK_MAX = 1024
REVISION = 5                  # digihealth's key revision 2, 128-bit key maps, encoder turns,
                              # several held keys (5; 4 holds one at a time)
MAX_HELD = {4: 1, 5: 4}       # keys held at once, by key revision
# PEEK reads DDR (cached view) and the 64 KB SRAM, and never the peripheral
# space (a read there can clear a status bit).
PEEK_RANGES = ((0x40000000, 0x48000000), (0x80000000, 0x80010000))
ORDERS = 24
RENDERS_PER_S = 1500          # the audio render: 48 kHz in blocks of 32


def pack7(data):
    """8-bit -> 7-bit: a header byte holding the high bits of up to 7 bytes."""
    data = bytes(data)
    out = bytearray()
    for i in range(0, len(data), 7):
        grp = data[i:i + 7]
        hdr = 0
        for k, b in enumerate(grp):
            if b & 0x80:
                hdr |= 1 << (6 - k)
        out.append(hdr)
        out.extend(b & 0x7F for b in grp)
    return bytes(out)


def unpack7(data):
    """7-bit -> 8-bit (a partial last group is allowed)."""
    data = bytes(data)
    out = bytearray()
    i = 0
    while i < len(data):
        hdr = data[i]
        i += 1
        for k in range(1, 8):
            if i >= len(data):
                break
            out.append(data[i] | ((hdr << k) & 0x80))
            i += 1
    return bytes(out)


def request(seq, cmd, payload=b''):
    return HEADER + pack7(struct.pack('>HB', seq & 0xFFFF, cmd) + payload) + b'\xF7'


def req_hello(seq):
    return request(seq, HELLO)


def req_stats(seq):
    return request(seq, STATS)


def req_caps(seq):
    return request(seq, CAPS)


def req_open(seq, session):
    return request(seq, OPEN, struct.pack('>I', session))


def req_action(seq, session, operation, action, key, duration):
    return request(seq, ACTION, struct.pack('>IIBBH', session, operation, action, key, duration))


def req_key_status(seq, session, operation):
    return request(seq, KEY_STATUS, struct.pack('>II', session, operation))


def req_cancel(seq, session):
    return request(seq, CANCEL, struct.pack('>I', session))


def req_peek(seq, addr, length):
    if not 0 < length <= PEEK_MAX:
        raise ValueError('length must be 1..%d' % PEEK_MAX)
    if not any(lo <= addr and addr + length <= hi for lo, hi in PEEK_RANGES):
        raise ValueError('0x%08x+%d is outside DDR (0x40000000-0x47ffffff) and '
                         'SRAM (0x80000000-0x8000ffff)' % (addr, length))
    return request(seq, PEEK, struct.pack('>IH', addr, length))


def parse(sx):
    """A reply -> {'seq', 'cmd', 'status', 'payload'}, or None if it is not one."""
    sx = bytes(sx)
    if not sx.startswith(HEADER) or not sx.endswith(b'\xF7'):
        return None
    msg = unpack7(sx[len(HEADER):-1])
    if len(msg) < 4:
        return None
    seq, cmd, status = struct.unpack_from('>HBB', msg)
    return {'seq': seq, 'cmd': cmd, 'status': status, 'payload': msg[4:]}


def decode_hello(p):
    proto_ver, uptime = struct.unpack_from('>HI', p)
    name = p[6:].split(b'\0')[0].decode('ascii', 'replace')
    return {'proto': proto_ver, 'uptime': uptime, 'name': name}


# The mod's snapshot, byte for byte (sysinfo.s, S_*): refreshed once a second.
STATS_FIELDS = ('layout', 'flags', 'seconds', 'window', 'render_ticks',
                'render_max', 'renders', 'idle_ticks', 'idle_render', 'heap_free',
                'heap_faults', 'heap_top_order', 'sample_free')
STATS_FMT = '>HHIIIIIIIIIII'


def decode_stats(p):
    d = dict(zip(STATS_FIELDS, struct.unpack_from(STATS_FMT, p)))
    off = struct.calcsize(STATS_FMT)
    d['free_blocks'] = list(struct.unpack_from('>%dH' % ORDERS, p, off))
    d['valid'] = bool(d['flags'] & 1)
    d['overlay'] = bool(d['flags'] & 2)
    w, n = d['window'], d['renders']
    if w and n:
        period = w / n                               # counter ticks per render
        d['counter_hz'] = period * RENDERS_PER_S
        d['dsp'] = d['render_ticks'] / w
        d['dsp_peak'] = d['render_max'] / period
        d['cpu'] = 1 - max(0, d['idle_ticks'] - d['idle_render']) / w
    return d


def decode_caps(p):
    """-> revision, product, OS major, OS minor, key bitmap, default tap, max tap,
    max hold, encoders"""
    revision, product, major, minor = struct.unpack_from('>HBBB', p)
    return (revision, product, major, minor, int.from_bytes(p[5:21], 'big'),
            *struct.unpack_from('>HHH', p, 21), p[27] if len(p) > 27 else 0)


def decode_peek(p):
    addr, = struct.unpack_from('>I', p)
    return {'addr': addr, 'data': p[4:]}


class _RequestRefused(ValueError):
    def __init__(self, message, status=1):
        super().__init__(message)
        self.status = status


class Diag:
    """The channel, over a send/receive Port. A stock OS never answers."""

    def __init__(self, port):
        self.port = port
        self.seq = 0x200

    def call(self, builder, *args, timeout=2.0, tries=2):
        """Only known reads may be retried; actions need explicit status recovery."""
        if builder not in (req_hello, req_stats, req_peek, req_caps, req_key_status,
                           pads.req_pad_caps, pads.req_capture_read):
            tries = 1
        if tries < 1:
            raise ValueError('tries must be positive')
        for attempt in range(tries):
            try:
                return self._call(builder, *args, timeout=timeout)
            except TimeoutError:
                if attempt == tries - 1:
                    raise

    def _call(self, builder, *args, timeout=2.0):
        self.seq = (self.seq + 1) & 0xFFFF or 1
        seq = self.seq
        req = builder(seq, *args)
        cmd = unpack7(req[len(HEADER):-1])[2]
        self.port.send(req)
        found = []

        def answered(msgs):
            for m in msgs[len(found):]:
                r = parse(m)
                if (CAPS <= cmd <= CANCEL or pads.PAD_CAPS <= cmd <= pads.PAD_NEXT) and bytes(m) != HEADER + pack7(unpack7(bytes(m)[6:-1])) + b'\xf7':
                    r = None
                found.append(r if r and r['seq'] == seq and r['cmd'] == cmd | 0x80 else None)
            return any(found)
        self.port.receive(timeout, until=answered)
        for r in found:
            if r:
                if r['status']:
                    raise _RequestRefused('the mod refused 0x%02x: %s'
                                          % (cmd, STATUS.get(r['status'], r['status'])), r['status'])
                return r['payload']
        raise TimeoutError('no answer: is a firmware with analoghealth installed?')


class RemoteKeys:
    """One bounded session. A timeout queries status and never replays an action."""

    def __init__(self, diag):
        self.diag = diag
        payload = diag.call(req_caps)
        if len(payload) != 28:
            raise ValueError('invalid key capabilities (an analoghealth older than key '
                             'revision %d?)' % REVISION)
        revision, product, major, minor, keys, default, tap, hold, encoders = decode_caps(payload)
        if (revision not in MAX_HELD or product not in PRODUCTS
                or (major, minor) != PRODUCTS[product][1]
                or keys != SUPPORTED.get(product) or not 1 <= default <= tap <= 250
                or not 1 <= hold <= 2000 or encoders != len(ENCODERS[product])):
            raise ValueError('unsupported key helper, device, OS or limits')
        self.default_tap, self.max_tap, self.max_hold = default, tap, hold
        self.revision, self.max_held = revision, MAX_HELD[revision]
        self.product, self.supported = product, keys
        self.keyids, self.encoders = KEYSETS[product], ENCODERS[product]
        self.session = secrets.randbits(32) or 1
        self.operation = 0
        self._report(diag.call(req_open, self.session, timeout=0.25), 0)

    def _report(self, payload, operation):
        if len(payload) != 27:
            raise ValueError('invalid key status length')
        owner, number, state, reason = struct.unpack_from('>IIBB', payload)
        owned, cleanup = int.from_bytes(payload[10:26], 'big'), payload[26]
        if (owner != self.session or (operation and number != operation)
                or state >= len(KEY_STATES) or reason not in STATUS
                or owned & ~self.supported or cleanup > 1):
            raise ValueError('invalid or mismatched key status')
        return dict(operation=number, state=KEY_STATES[state], reason=STATUS[reason],
                    owned=owned, cleanup=cleanup)

    def status(self, operation=None):
        # The OS's MIDI input task stalls for a moment while it loads a project (a YES
        # that starts one): keep asking for up to 3 s before giving up.
        operation = self.operation if operation is None else operation
        deadline = time.monotonic() + 3
        while True:
            try:
                return self._report(self.diag.call(req_key_status, self.session, operation,
                                                  timeout=0.25), operation)
            except TimeoutError:
                if time.monotonic() >= deadline:
                    raise

    def _wait(self, report, cancelling=False):
        deadline = time.monotonic() + 3
        while report['state'] == 'accepted' or report['cleanup']:
            if time.monotonic() >= deadline:
                raise TimeoutError('key cleanup or action still pending; not replayed')
            time.sleep(0.01)
            report = self.status(0 if cancelling else None)
        if cancelling:
            if report['owned']:
                raise TimeoutError('cleanup unconfirmed; firmware hold lease remains in force')
        elif report['state'] != 'completed':
            raise RuntimeError('key action %s: %s' % (report['state'], report['reason']))
        return report

    def action(self, action, key, duration=0):
        if action == TURN:
            ticks, step = duration & 0xFF, (duration >> 8) - (duration >> 7 & 0x100)
            if (key not in self.encoders or not 1 <= ticks <= MAX_TURNS or not step
                    or abs(step) > MAX_STEP):
                raise ValueError('unsupported encoder or turn')
            code = self.encoders[key]
        else:
            if key not in MAPS[self.product] or action not in (PRESS, RELEASE, TAP):
                raise ValueError('unsupported key or action')
            limit = self.max_hold if action == PRESS else self.max_tap
            if action == RELEASE and duration != 0 or action != RELEASE and not 1 <= duration <= limit:
                raise ValueError('invalid key duration')
            code = self.keyids[key]
        self.operation += 1
        try:
            payload = self.diag.call(req_action, self.session, self.operation, action,
                                     code, duration, timeout=0.25)
            report = self._report(payload, self.operation)
        except _RequestRefused:
            self.operation -= 1
            raise
        except TimeoutError:
            report = self.status()
        return self._wait(report)

    def press(self, key, duration=None):
        return self.action(PRESS, key, self.max_hold if duration is None else duration)

    def release(self, key):
        return self.action(RELEASE, key)

    def tap(self, key, duration=None):
        return self.action(TAP, key, self.default_tap if duration is None else duration)

    def turn(self, encoder, delta, step=1):
        """Turn `encoder` (A-H or A-J, LEVEL) by `delta` counts, `step` counts per UI tick
        (20 a second), as TURN actions of up to MAX_TURNS ticks each."""
        if not delta or not 1 <= step <= MAX_STEP:
            raise ValueError('use a nonzero delta and a step of 1..%d' % MAX_STEP)
        sign, left = (1 if delta > 0 else -1), abs(delta)
        while left:
            size = min(step, left)
            ticks = min(MAX_TURNS, left // size)
            self.action(TURN, encoder, ((sign * size) & 0xFF) << 8 | ticks)
            left -= size * ticks

    def pads(self, events, between=None):
        """Run one pad script (analogpads: [(ms, header, data)]) as one operation and
        wait until the firmware has played it and let go of every pad. `between(ms)` runs
        while waiting, with the milliseconds since the script was sent (a shot during the
        press, say)."""
        pads.check(events)
        sent = time.monotonic()
        self.operation += 1
        try:
            payload = self.diag.call(pads.req_pad_run, self.session, self.operation, events,
                                     timeout=0.25)
            report = self._report(payload, self.operation)
        except _RequestRefused:
            self.operation -= 1
            raise
        except TimeoutError:
            report = self.status()
        deadline = time.monotonic() + events[-1][0] / 1000 + 3
        while report['state'] == 'accepted':
            if time.monotonic() >= deadline:
                raise TimeoutError('pad script still running; not replayed')
            if between:
                between((time.monotonic() - sent) * 1000)
            time.sleep(0.02)
            report = self.status()
        if report['state'] != 'completed':
            raise RuntimeError('pad script %s: %s' % (report['state'], report['reason']))
        return report

    def _pad_op(self, builder, events, *after):
        """One PAD RUN or PAD NEXT: 'ok', 'busy' (the queue is full or the player
        still busy: ask again) or 'stale' (PAD NEXT: nothing is left playing to
        follow)."""
        self.operation += 1
        try:
            self._report(self.diag.call(builder, self.session, self.operation, *after, events,
                                        timeout=0.25), self.operation)
            return 'ok'
        except _RequestRefused as e:
            self.operation -= 1
            if e.status == 3:
                return 'busy'
            if e.status == 4 and builder is pads.req_pad_next:
                return 'stale'
            raise
        except TimeoutError:
            if self.status(0)['operation'] == self.operation:
                return 'ok'
            self.operation -= 1
            return 'busy'

    def pad_chain(self, plan, between=None):
        """Play [(start ms, script)] (analogpads.replay_plan) as a chain: the first
        with PAD RUN, each next with PAD NEXT as soon as the firmware's queue has
        room, timed there from the start of the one before it, so the host's own
        latency never reaches the timing. When the chain has played out before the
        next script arrives (a pause longer than the firmware queues ahead, or a
        host that fell behind), that script starts a new chain with PAD RUN at its
        own time by the host's clock. `between` runs while waiting (padplay's log
        reader). -> (times the chain played out before its next script arrived,
        the latest of the host-timed starts, in ms)."""
        restarts, late, begin, prev = 0, 0.0, None, None

        def pause(seconds=0.02):
            # a full queue holds a few hundred ms: no need to ask more often
            if between:
                between()
            time.sleep(seconds)
        for start, s in plan:
            pads.check(s)
            while True:
                if prev is not None and start - prev <= pads.MAX_MS:
                    got = self._pad_op(pads.req_pad_next, s, start - prev)
                    if got == 'stale':
                        report = self.status()
                        if report['state'] not in ('accepted', 'completed'):
                            raise RuntimeError('pad script %s: %s' % (report['state'], report['reason']))
                        if report['state'] == 'completed':
                            prev = None
                            restarts += 1
                        got = 'busy'
                else:
                    if begin is not None:
                        wait = begin + start / 1000 - time.monotonic()
                        while wait > 0:
                            pause(min(wait, 0.02))
                            wait = begin + start / 1000 - time.monotonic()
                        late = max(late, -wait * 1000)
                    sent = time.monotonic()
                    got = self._pad_op(pads.req_pad_run, s)
                    if got == 'ok' and begin is None:
                        begin = sent - start / 1000
                if got == 'ok':
                    break
                pause()
            prev = start
        deadline = begin + (plan[-1][0] + plan[-1][1][-1][0]) / 1000 + 3
        report = self.status()
        while report['state'] == 'accepted':
            if time.monotonic() >= deadline:
                raise TimeoutError('pad scripts still running; not replayed')
            pause()
            report = self.status()
        if report['state'] != 'completed':
            raise RuntimeError('pad script %s: %s' % (report['state'], report['reason']))
        return restarts, late

    def cancel(self):
        try:
            report = self._report(self.diag.call(req_cancel, self.session, timeout=0.25), 0)
        except TimeoutError:
            report = self.status(0)
            if report['state'] not in ('cancelled', 'expired') and not report['cleanup']:
                raise TimeoutError('cancel delivery unknown; not replayed, hold lease remains in force')
        return self._wait(report, cancelling=True)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.cancel()


def key_sequence(text):
    """'KEY' or 'HOLD(TAP)' or 'HOLD1+HOLD2(TAP)', comma-separated -> [(holds, tap)]:
    the holds are pressed in order before the tap and let go in reverse after it."""
    steps = []
    for token in text.split(','):
        match = re.fullmatch(r'([A-Z0-9_]+(?:\s*\+\s*[A-Z0-9_]+)*)(?:\s*\(\s*([A-Z0-9_]+)\s*\))?',
                             token.strip())
        if not match or '+' in match.group(1) and not match.group(2):
            raise argparse.ArgumentTypeError('use KEY, HOLD(TAP) or HOLD+HOLD(TAP), separated by commas')
        outer, inner = match.groups()
        holds, tap = ([h.strip() for h in outer.split('+')], inner) if inner else ([], outer)
        if tap not in ALL_KEYS or any(h not in ALL_KEYS for h in holds):
            raise argparse.ArgumentTypeError('unknown button in %r' % token.strip())
        if tap in holds or len(set(holds)) != len(holds):
            raise argparse.ArgumentTypeError('held and tapped buttons must differ')
        steps.append((holds, tap))
    return steps


def play_sequence(keys, steps, tap_ms=None, hold_ms=None):
    """Run key_sequence steps: each one's holds in order, its tap, the holds let go in reverse."""
    for holds, tap in steps:
        most = getattr(keys, 'max_held', 1)
        if len(holds) > most:
            raise ValueError('this firmware holds %d key(s) at once (key revision %s); %s needs %d'
                             % (most, getattr(keys, 'revision', '?'), '+'.join(holds), len(holds)))
        for h in holds:
            keys.press(h, hold_ms)
        keys.tap(tap, tap_ms)
        for h in reversed(holds):
            keys.release(h)


def key_args(args):
    parser = argparse.ArgumentParser(
        description='INPUT is KEY, HOLD(TAP) or HOLD+HOLD(TAP) (holds pressed in order), '
                    'or a comma-separated list of them.',
        allow_abbrev=False)
    parser.add_argument('steps', type=key_sequence, metavar='INPUT')
    parser.add_argument('--tap-ms', type=int, default=100)
    parser.add_argument('--hold-ms', type=int, default=2000)
    args = parser.parse_args(args)
    if not 1 <= args.tap_ms <= 250 or not 1 <= args.hold_ms <= 2000:
        parser.error('use tap 1..250 ms and hold 1..2000 ms')
    return args


def turn_args(args):
    parser = argparse.ArgumentParser(
        prog='analogusb.py turn',
        description='Turn an encoder as the panel does: ENCODER is A-H (Rytm) or A-J (A4), or '
                    'LEVEL; DELTA the counts (negative turns left).', allow_abbrev=False)
    parser.add_argument('encoder', type=str.upper, choices=sorted(ALL_ENCODERS), metavar='ENCODER')
    parser.add_argument('delta', type=int)
    parser.add_argument('--step', type=int, default=1,
                        help='counts per UI tick, 1..%d (default 1; larger is faster and '
                             'meets the OS acceleration)' % MAX_STEP)
    parser.add_argument('--hold', choices=sorted(ALL_KEYS), metavar='KEY',
                        help='hold this key during the turn (e.g. FUNC)')
    args = parser.parse_args(args)
    if not args.delta or not 1 <= args.step <= MAX_STEP:
        parser.error('use a nonzero DELTA and --step 1..%d' % MAX_STEP)
    if args.hold and -(-abs(args.delta) // args.step) > MAX_TURNS:
        parser.error('with --hold, a turn must fit one action (%d ticks): raise --step'
                     % MAX_TURNS)
    return args


def cmd_turn(diag, args):
    with RemoteKeys(diag) as keys:
        if args.encoder not in keys.encoders or args.hold and args.hold not in keys.keyids:
            raise ValueError('the connected device does not have that encoder or button')
        if args.hold:
            keys.press(args.hold)
        keys.turn(args.encoder, args.delta, args.step)
        if args.hold:
            keys.release(args.hold)
    print('turn completed')


def cmd_keys(diag, args):
    with RemoteKeys(diag) as keys:
        if any(k not in MAPS[keys.product] for holds, tap in args.steps for k in holds + [tap]):
            raise ValueError('the connected device does not have the requested button')
        play_sequence(keys, args.steps, args.tap_ms, args.hold_ms)
    print('key actions completed; synthetic keys released')


def cmd_cfw(d, _args):
    h = decode_hello(d.call(req_hello))
    print('%s  (channel v%d, up %d s)' % (h['name'], h['proto'], h['uptime']))


def show_stats(s):
    print('second %d%s' % (s['seconds'], '' if s['overlay'] else '  (SYSTEM INFO off)'))
    if 'dsp' in s:
        print('  CPU %5.1f %%   DSP %5.1f %%   DSP peak %5.1f %%'
              % (100 * s['cpu'], 100 * s['dsp'], 100 * s['dsp_peak']))
        print('  %d renders in %d counter ticks: counter %.3f MHz'
              % (s['renders'], s['window'], s['counter_hz'] / 1e6))
    else:
        print('  no renders this second (audio stopped, or the counter is not running)')
    blocks = ', '.join('%d:%d' % (o, n) for o, n in enumerate(s['free_blocks']) if n)
    print('  heap free %.2f MB (%d bytes), faults %d, free blocks by order: %s'
          % (s['heap_free'] / 1048576, s['heap_free'], s['heap_faults'], blocks or '-'))
    if s['sample_free']:
        print('  sample memory free %.2f MB' % (s['sample_free'] / 1048576))


def cmd_stats(d, args):
    last = None
    for _ in range(int(args[0]) if args else 1):
        while True:
            s = decode_stats(d.call(req_stats))
            if s['seconds'] != last:
                break
            time.sleep(0.1)
        last = s['seconds']
        show_stats(s)


def _range(args):
    addr = int(args[0], 0)
    length = int(args[1], 0) if len(args) > 1 else 256
    return addr, length


def peek(d, addr, length):
    out = b''
    while len(out) < length:
        n = min(PEEK_MAX, length - len(out))
        r = decode_peek(d.call(req_peek, addr + len(out), n))
        if r['addr'] != addr + len(out) or len(r['data']) != n:
            raise ValueError('short or misplaced PEEK reply at 0x%08x' % r['addr'])
        out += r['data']
    return out


def cmd_peek(d, args):
    addr, length = _range(args)
    data = peek(d, addr, length)
    for i in range(0, len(data), 16):
        row = data[i:i + 16]
        print('%08x  %-48s %s' % (addr + i, row.hex(' '),
                                   ''.join(chr(b) if 32 <= b < 127 else '.' for b in row)))


def cmd_dump(d, args):
    addr, length = _range(args[:2])
    data = peek(d, addr, length)
    with open(args[2], 'wb') as fh:
        fh.write(data)
    print('saved %s (%d bytes from 0x%08x)' % (args[2], len(data), addr))


def selftest():
    sx = req_peek(7, 0x4020bb14, 20)
    assert sx[:6] == HEADER and all(b < 0x80 for b in sx[1:-1])
    assert unpack7(sx[6:-1]) == struct.pack('>HBIH', 7, PEEK, 0x4020bb14, 20)
    reply = HEADER + pack7(struct.pack('>HBBI', 7, PEEK | 0x80, 0, 0x4020bb14)
                           + bytes(range(20))) + b'\xF7'
    r = parse(reply)
    assert r['status'] == 0 and decode_peek(r['payload'])['data'] == bytes(range(20))
    req_peek(1, 0x80000000, 16)
    req_peek(1, 0x8000FFF0, 16)
    for bad in ((0xFC07000C, 4), (0x47FFFFFF, 2), (0x40000000, 0),
                (0x40000000, PEEK_MAX + 1), (0x7FFFFFFE, 4), (0x8000FFFE, 4)):
        try:
            req_peek(1, *bad)
        except ValueError:
            continue
        raise AssertionError('accepted %r' % (bad,))
    st = struct.pack(STATS_FMT, 1, 1, 60, 66000000, 39600000, 30000, 1500,
                     26000000, 0, 14000000, 0, 20, 60000000) + bytes(2 * ORDERS)
    assert len(st) == 96
    s = decode_stats(st)
    assert round(s['counter_hz']) == 66000000 and round(s['dsp'], 2) == 0.60
    print('selftest ok')


def decode_file(path):
    """Print every analoghealth reply in a file of raw MIDI bytes (an emulator's
    USB MIDI output, or a capture)."""
    with open(path, 'rb') as fh:
        data = fh.read()
    at = 0
    while True:
        start = data.find(HEADER, at)
        end = data.find(b'\xF7', start) if start >= 0 else -1
        if end < 0:
            break
        at = end + 1
        r = parse(data[start:at])
        if not r or not r['cmd'] & 0x80:
            continue
        cmd, p = r['cmd'] & 0x7F, r['payload']
        print('seq %d  cmd 0x%02x  status %d (%s)'
              % (r['seq'], cmd, r['status'], STATUS.get(r['status'], '?')))
        if r['status']:
            continue
        if cmd == HELLO:
            h = decode_hello(p)
            print('  %s  (channel v%d, up %d s)' % (h['name'], h['proto'], h['uptime']))
        elif cmd == STATS:
            show_stats(decode_stats(p))
        elif cmd == CAPS:
            revision, product, major, minor, keys, default, tap, hold, enc = decode_caps(p)
            print('  key revision %d, product %d, OS %d.%d, %d keys%s, %d encoders, tap %d of %d ms, '
                  'hold %d ms' % (revision, product, major, minor, bin(keys).count('1'),
                                  '' if keys == SUPPORTED.get(product) else ' (not this map)', enc,
                                  default, tap, hold))
        else:
            print('  payload', p.hex(' '))


class PanelWatch:
    """The panel's button state as the OS sees it, read with PEEK (no firmware
    support needed). Each key's place comes from the unit's own table, read
    once; the device's mod.json names the two addresses. Held remote keys
    show too."""

    def __init__(self, diag):
        import json
        import os
        product = decode_caps(diag.call(req_caps))[1]
        if product not in PRODUCTS:
            raise ValueError('unsupported device (product %d)' % product)
        root = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
        manifest = os.path.join(root, 'a4', 'mod.json') if product == A4 else os.path.join(root, 'mod.json')
        with open(manifest) as fh:
            mod = json.load(fh)
        defsym = mod['defsym']
        self.diag = diag
        self.groups = 12 if product == A4 else 11
        self.state = int(defsym['KEY_STATE'], 16)
        table = int(defsym['KEY_TABLE'], 16)
        if '-DRK_KEY_TABLE_DIRECT' not in mod.get('cflags', []):
            table, = struct.unpack('>I', peek(diag, table, 4))
        codes = struct.unpack('>%dI' % (8 * self.groups), peek(diag, table, 32 * self.groups))
        names = {code: name for name, code in MAPS[product].items()}
        self.slots = {(i // 8, 1 << i % 8): names[c] for i, c in enumerate(codes) if c in names}

    def held(self):
        """-> the set of key names down now."""
        masks = peek(self.diag, self.state, self.groups)
        return {name for (g, m), name in self.slots.items() if masks[g] & m}

    def wait_for(self, name, timeout=60.0, interval=0.05):
        """Block until `name` is down; -> True, or False after `timeout` s."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if name in self.held():
                return True
            time.sleep(interval)
        return False


def cmd_watch(d, args):
    """watch [SECONDS] [--until KEY]: print keys as they go down and up."""
    until = None
    if '--until' in args:
        i = args.index('--until')
        until = args[i + 1]
        if until not in ALL_KEYS:
            raise ValueError('unknown button %r' % until)
        args = args[:i] + args[i + 2:]
    seconds = float(args[0]) if args else 30.0
    w = PanelWatch(d)
    last, end = set(), time.monotonic() + seconds
    print('watching %d keys for %g s (Ctrl+C stops)' % (len(w.slots), seconds))
    while time.monotonic() < end:
        now = w.held()
        for name in sorted(now - last):
            print('%8.2f  %s down' % (time.monotonic() - end + seconds, name))
        for name in sorted(last - now):
            print('%8.2f  %s up' % (time.monotonic() - end + seconds, name))
        last = now
        if until in now:
            print('%s is down' % until)
            return
        time.sleep(0.05)
    if until:
        raise TimeoutError('%s was not pressed' % until)


def pad_caps(d):
    try:
        caps = pads.decode_caps(d.call(pads.req_pad_caps))
    except _RequestRefused:
        raise ValueError('this firmware has no pad commands (an analoghealth before 1.3, '
                         'or not a Rytm)') from None
    if caps['revision'] not in (1, 2) or caps['pads'] != len(pads.PADS):
        raise ValueError('unsupported pad revision %d' % caps['revision'])
    return caps


def pad_args(args):
    parser = argparse.ArgumentParser(
        prog='analogusb.py pad',
        description='Strike Rytm pads as the panel does: PADS is a pad name (BD..CB) or '
                    'several, comma-separated, struck one after another.', allow_abbrev=False)
    parser.add_argument('pads', type=lambda t: [p.strip().upper() for p in t.split(',')])
    force = parser.add_mutually_exclusive_group()
    force.add_argument('--vel', type=int, default=100,
                       help='the velocity the OS should see, 1..127 (default 100)')
    force.add_argument('--raw', type=int, help='the raw force the panel sends, 0..255')
    parser.add_argument('--hold', type=int, default=100, help='ms between strike and release')
    parser.add_argument('--gap', type=int, default=150, help='ms between pads')
    parser.add_argument('--pressure', type=lambda t: [int(v) for v in t.split(',')], default=[],
                        help='pressure values, 0..255, sent while held')
    parser.add_argument('--every', type=int, default=10, help='ms between pressure values')
    parser.add_argument('--key', choices=('bridge', 'panel', 'none'), default='bridge',
                        help="the pad's key: through the input bridge (as the panel's button "
                             "messages), as a panel key message, or not at all")
    args = parser.parse_args(args)
    if any(p not in pads.PADS for p in args.pads):
        parser.error('pads are %s' % ','.join(pads.PADS))
    if args.raw is None and not 1 <= args.vel <= 127 or args.raw is not None and not 0 <= args.raw <= 255:
        parser.error('use --vel 1..127 or --raw 0..255')
    if not 1 <= args.hold <= 5000 or not 0 <= args.gap <= 5000 or not 1 <= args.every <= 1000:
        parser.error('use --hold 1..5000, --gap 0..5000 and --every 1..1000 ms')
    if any(not 0 <= v <= 255 for v in args.pressure):
        parser.error('pressure values are 0..255')
    return args


def cmd_pad(d, args):
    caps = pad_caps(d)
    raw = args.raw if args.raw is not None else pads.raw_for(args.vel, caps['threshold'])
    with RemoteKeys(d) as keys:
        for i, name in enumerate(args.pads):
            if i:
                time.sleep(args.gap / 1000)
            keys.pads(pads.hit(name, raw, args.hold, args.pressure, args.every,
                               None if args.key == 'none' else args.key))
            print('%s struck: raw %d, velocity %d' % (name, raw,
                                                       pads.velocity(raw, caps['threshold'])))


def cmd_padcap(d, args):
    """padcap [SECONDS] [--out FILE.json]: log the panel's bytes and print them."""
    out = None
    if '--out' in args:
        i = args.index('--out')
        out, args = args[i + 1], args[:i] + args[i + 2:]
    seconds = float(args[0]) if args else 10.0
    caps = pad_caps(d)
    entries, at = [], 0
    d.call(pads.req_capture, 1)
    print('logging the panel for %g s: play the pads now' % seconds)
    end = time.monotonic() + seconds
    try:
        while True:
            done = time.monotonic() >= end
            if done:
                d.call(pads.req_capture, 0)
            while True:
                logged, first, got = pads.decode_read(d.call(pads.req_capture_read, at))
                if first > at:
                    print('  (%d bytes lost: the log holds %d)' % (first - at, caps['log']))
                entries += got
                at = first + len(got)
                if at >= logged:
                    break
            if done:
                break
            time.sleep(0.05)
    finally:
        try:
            d.call(pads.req_capture, 0)
        except Exception:
            pass
    msgs = pads.messages(entries)
    t0 = msgs[0]['t'] if msgs else 0
    for m in msgs:
        print('%9.2f ms  %s%s' % (((m['t'] - t0) & 0xFFFFFFFF) / pads.TICKS_PER_MS,
                                  pads.describe(m, caps['threshold']), '  (ours)' if m['ours'] else ''))
    print('%d bytes, %d messages; threshold %d' % (len(entries), len(msgs), caps['threshold']))
    if out:
        pads.save(out, entries, caps['threshold'])
        print('saved %s' % out)


def _drain(d, at, entries):
    while True:
        logged, first, got = pads.decode_read(d.call(pads.req_capture_read, at))
        entries += got
        at = first + len(got)
        if at >= logged:
            return at


def cmd_padplay(d, args):
    """padplay FILE.json [--pads BD,SD] [--log OUT.json]: send the logged pad messages
    again; --log also logs what reached the panel receiver meanwhile and compares it
    with the original."""
    which = out = None
    if '--pads' in args:
        i = args.index('--pads')
        which, args = [p.strip().upper() for p in args[i + 1].split(',')], args[:i] + args[i + 2:]
    if '--log' in args:
        i = args.index('--log')
        out, args = args[i + 1], args[:i] + args[i + 2:]
    entries, _threshold = pads.load(args[0])
    caps = pad_caps(d)
    # A firmware with PAD NEXT (revision 2) queues the scripts and keeps their
    # timing itself; before it, the host starts each one at its time.
    chained = caps['queue'] > 0
    plan = pads.replay_plan(pads.messages(entries), which, carry=chained)
    if not plan:
        raise ValueError('the log holds no pad messages')
    scripts = [s for _, s in plan]
    got, at, late, dry = [], [0], 0.0, 0
    if out:
        d.call(pads.req_capture, 1)

    def drain():
        if out:
            at[0] = _drain(d, at[0], got)
    try:
        with RemoteKeys(d) as keys:
            if chained:
                dry, late = keys.pad_chain(plan, between=drain)
            else:
                begin = time.monotonic()
                for start, s in plan:
                    # each script at its own place in the take, not straight after the last
                    wait = begin + start / 1000 - time.monotonic()
                    if wait > 0:
                        time.sleep(wait)
                    else:
                        late = max(late, -wait * 1000)
                    keys.pads(s)
                    drain()
    finally:
        if out:
            d.call(pads.req_capture, 0)
    if chained:
        print('played %d events in %d chained scripts; the queue ran dry %d times%s'
              % (sum(map(len, scripts)), len(scripts), dry,
                 ' (latest host-timed start %.1f ms late)' % late if late else ''))
    else:
        print('played %d events in %d scripts; latest script start %.1f ms late'
              % (sum(map(len, scripts)), len(scripts), late))
    if out:
        drain()
        pads.save(out, got, caps['threshold'])
        want = [m for m in pads.messages(entries) if not m['ours'] and len(m['bytes']) == 2
                and m['bytes'][0] >> 4 in (pads.PRESSURE, pads.STRIKE, pads.RELEASE, pads.KEY)
                and (which is None or pads.PADS[m['bytes'][0] & 15] in which)]
        sent = [m for m in pads.messages(got) if m['ours']]
        print('original vs replayed (ms from each one\'s first):')
        w0 = want[0]['t'] if want else 0
        s0 = sent[0]['t'] if sent else 0
        same = len(want) == len(sent)
        for i in range(max(len(want), len(sent))):
            a = want[i] if i < len(want) else None
            b = sent[i] if i < len(sent) else None
            ta = ((a['t'] - w0) & 0xFFFFFFFF) / pads.TICKS_PER_MS if a else None
            tb = ((b['t'] - s0) & 0xFFFFFFFF) / pads.TICKS_PER_MS if b else None
            ok = a and b and a['bytes'] == b['bytes'] and abs(ta - tb) <= 2
            same = same and bool(ok)
            print('  %-34s %-34s %s' % ('%9.2f %s' % (ta, pads.describe(a, caps['threshold'])) if a else '-',
                                        '%9.2f %s' % (tb, pads.describe(b, caps['threshold'])) if b else '-',
                                        'ok' if ok else 'DIFFERS'))
        print('replay %s; saved %s' % ('matches (bytes equal, times within 2 ms)' if same else 'DIFFERS', out))


COMMANDS = {'cfw': cmd_cfw, 'stats': cmd_stats, 'peek': cmd_peek, 'dump': cmd_dump, 'keys': cmd_keys,
            'turn': cmd_turn, 'pad': cmd_pad, 'padcap': cmd_padcap, 'padplay': cmd_padplay,
            'watch': cmd_watch}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    name = None
    if argv[:1] == ['--port']:
        name, argv = argv[1], argv[2:]
    if argv[:1] == ['selftest']:
        selftest()
        return
    if argv[:1] == ['decode'] and len(argv) == 2:
        decode_file(argv[1])
        return
    if not argv or argv[0] not in COMMANDS:
        sys.exit(__doc__)
    args = (key_args(argv[1:]) if argv[0] == 'keys' else turn_args(argv[1:]) if argv[0] == 'turn'
            else pad_args(argv[1:]) if argv[0] == 'pad' else argv[1:])
    if sys.platform == 'win32':
        from winmidi import Port
    else:
        from midimido import Port
    port = Port(name)
    try:
        COMMANDS[argv[0]](Diag(port), args)
    finally:
        port.close()


if __name__ == '__main__':
    main()
