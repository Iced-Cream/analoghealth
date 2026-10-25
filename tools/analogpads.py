# SPDX-License-Identifier: GPL-2.0-or-later
"""The Analog Rytm MKII's pads over analoghealth's USB channel: the panel's own
pad messages fed to the OS's panel receiver, and a log of the bytes the panel
sends (docs/PADS.md).

A panel message is a header byte (high nibble the type, low nibble the index)
and a data byte. For pad p (0 BD .. 11 CB):
    9p v   pressure while held       Ap r   struck, r the raw force
    Bp 0   released                  Cp k   the pad's key, k nonzero down
and, not a panel message, 0p k: the pad's key bit through the input bridge, as
the panel's button-group messages give it. The OS turns r into a velocity
with its own threshold t (velocity() below).
"""
import json
import struct

PAD_CAPS, CAPTURE, CAPTURE_READ, PAD_RUN = range(0x18, 0x1C)
PADS = ('BD', 'SD', 'RS', 'CP', 'BT', 'LT', 'MT', 'HT', 'CH', 'OH', 'CY', 'CB')
PRESSURE, STRIKE, RELEASE, KEY, BRIDGE = 0x9, 0xA, 0xB, 0xC, 0x0
KINDS = {PRESSURE: 'pressure', STRIKE: 'strike', RELEASE: 'release', KEY: 'key', BRIDGE: 'bridge key'}
MAX_EVENTS, MAX_MS = 48, 10000
TICKS_PER_MS = 132000                    # the log's clock, DTIM0 at the bus clock
PAD_SLOT = 72                            # pad p is panel slot 72 + p: group 9 bit p, then group 10
TYPES = {0x2: 'buttons', 0x3: 'encoder', 0x4: 'value', 0x7: 'long', 0x9: 'pressure',
         0xA: 'strike', 0xB: 'release', 0xC: 'key', 0xF: 'long'}


def _s32(x):
    x &= 0xFFFFFFFF
    return x - (1 << 32) if x & 0x80000000 else x


def _cdiv(a, b):
    """C's signed division (truncates toward zero)."""
    q = abs(a) // abs(b)
    return q if (a < 0) == (b < 0) else -q


def velocity(raw, threshold):
    """The OS's curve (panel receiver, OS 1.74), in its 32-bit arithmetic: the
    velocity a strike with raw force `raw` gives, 0 when nothing is posted;
    the UI clamps it to 127."""
    v = _cdiv(_s32((raw - threshold) * 11), 4)
    v = _cdiv(_s32(v * 255), 255 - threshold)
    v = min(v, 255)
    out = (_s32(_s32(v * v) * 258) >> 16) & 0xFFFF      # swap, ext.l: the high word
    out = out - 0x10000 if out & 0x8000 else out
    return min(out, 127) if out > 0 else 0


def raw_for(target, threshold):
    """The smallest raw force above the threshold that gives `target` (1..127)."""
    for raw in range(threshold + 1, 256):
        if velocity(raw, threshold) >= target:
            return raw
    raise ValueError('no raw force reaches velocity %d (threshold %d)' % (target, threshold))


def req(seq, cmd, payload=b''):
    from analogusb import request
    return request(seq, cmd, payload)


def req_pad_caps(seq):
    return req(seq, PAD_CAPS)


def req_capture(seq, mode):
    return req(seq, CAPTURE, bytes([mode]))


def req_capture_read(seq, start):
    return req(seq, CAPTURE_READ, struct.pack('>I', start))


def req_pad_run(seq, session, operation, events):
    body = bytearray(struct.pack('>IIB', session, operation, len(events)))
    for ms, header, data in events:
        body += struct.pack('>HBB', ms, header, data)
    return req(seq, PAD_RUN, bytes(body))


def decode_caps(p):
    if len(p) != 10:
        raise ValueError('invalid pad capabilities')
    revision, pads, events, max_ms, log, threshold, on = struct.unpack('>HBBHHBB', p)
    return dict(revision=revision, pads=pads, events=events, max_ms=max_ms, log=log,
                threshold=threshold, remote_keys=bool(on))


def decode_read(p):
    logged, first, n = struct.unpack_from('>IIB', p)
    if len(p) != 9 + 6 * n:
        raise ValueError('invalid capture read')
    entries = [struct.unpack_from('>IBB', p, 9 + 6 * i) for i in range(n)]
    return logged, first, entries


def check(events):
    """A script: [(ms, header, data)], times rising, within the firmware's limits."""
    if not 1 <= len(events) <= MAX_EVENTS:
        raise ValueError('a script holds 1..%d events' % MAX_EVENTS)
    last = 0
    for ms, header, data in events:
        if not last <= ms <= MAX_MS or header >> 4 not in KINDS or header & 15 >= len(PADS) \
                or not 0 <= data <= 255:
            raise ValueError('bad event %r' % ((ms, header, data),))
        last = ms
    return events


def hit(pad, raw, hold_ms=100, pressure=(), every_ms=10, key='bridge'):
    """One strike of `pad` with raw force `raw`, held `hold_ms`: the key down first
    (key 'bridge', 'panel' or None), then the strike, `pressure` values every
    `every_ms`, then the release and the key up."""
    p = PADS.index(pad)
    ev = []
    if key == 'bridge':
        ev.append((0, BRIDGE << 4 | p, 1))
    elif key == 'panel':
        ev.append((0, KEY << 4 | p, 1))
    ev.append((0, STRIKE << 4 | p, raw))
    for i, v in enumerate(pressure):
        at = (i + 1) * every_ms
        if at >= hold_ms:
            raise ValueError('pressure runs past the hold: raise --hold or lower --every')
        ev.append((at, PRESSURE << 4 | p, v))
    ev.append((hold_ms, RELEASE << 4 | p, 0))
    if key == 'bridge':
        ev.append((hold_ms, BRIDGE << 4 | p, 0))
    elif key == 'panel':
        ev.append((hold_ms, KEY << 4 | p, 0))
    return check(ev)


def messages(entries):
    """Log entries [(time, byte, flags)] -> panel messages
    [{'t': ticks, 'bytes': [...], 'ours': bool}], split where the receiver
    took a byte as a header (flag bit 1)."""
    out = []
    for t, byte, flags in entries:
        if flags & 2 or not out:
            out.append({'t': t, 'bytes': [byte], 'ours': bool(flags & 1)})
        else:
            out[-1]['bytes'].append(byte)
    return out


def describe(msg, threshold=None):
    b = msg['bytes']
    kind, index = b[0] >> 4, b[0] & 15
    name = TYPES.get(kind, 'type %X' % kind)
    data = ' '.join('%02X' % x for x in b)
    if kind in (PRESSURE, STRIKE, RELEASE, KEY) and index < len(PADS):
        text = '%s %s' % (PADS[index], name)
        if len(b) > 1 and kind == STRIKE:
            text += ' raw %d' % b[1]
            if threshold is not None:
                text += ' (velocity %d)' % velocity(b[1], threshold)
        elif len(b) > 1 and kind == PRESSURE:
            text += ' %d' % b[1]
        elif len(b) > 1 and kind == KEY:
            text += ' down' if b[1] else ' up'
    elif kind == 0x2 and len(b) > 1:
        pads = [PADS[s - PAD_SLOT] for s in range(index * 8, index * 8 + 8)
                if PAD_SLOT <= s < PAD_SLOT + len(PADS) and b[1] >> (s % 8) & 1]
        text = 'buttons group %d = %s%s' % (index, format(b[1], '08b'),
                                            ' (pads %s)' % ','.join(pads) if pads else '')
    elif kind == 0x3 and len(b) > 1:
        text = 'encoder %d %+d' % (index, b[1] - 256 if b[1] > 127 else b[1])
    else:
        text = name
    return '%-6s %s' % (data, text)


def replay_script(msgs, pads=None):
    """Captured panel messages -> scripts that send the pad messages again with
    their timing: pressure, strike, release and key messages as they were, and
    button-group changes to a pad's bit as bridge key events. Split into
    scripts of at most MAX_EVENTS events and MAX_MS each."""
    wanted = set(range(len(PADS))) if pads is None else {PADS.index(p) for p in pads}
    events, held = [], {}
    for m in msgs:
        b = m['bytes']
        if m['ours'] or len(b) != 2:
            continue
        kind, index = b[0] >> 4, b[0] & 15
        if kind in (PRESSURE, STRIKE, RELEASE, KEY) and index in wanted:
            events.append((m['t'], b[0], b[1]))
        elif kind == 0x2:
            for s in range(index * 8, index * 8 + 8):
                p = s - PAD_SLOT
                if p in wanted and 0 <= p < len(PADS):
                    down = b[1] >> (s % 8) & 1
                    if held.get(p, 0) != down:
                        events.append((m['t'], BRIDGE << 4 | p, down))
                        held[p] = down
    if not events:
        return []
    # Split only where no pad is down: the firmware lets go of every pad at the
    # end of a script.
    scripts, cur, base, down = [], [], None, set()
    for t, h, d in events:
        if base is not None and not down and (len(cur) == MAX_EVENTS
                                              or (t - base) // TICKS_PER_MS > MAX_MS):
            scripts.append(cur)
            cur, base = [], None
        if base is None:
            base = t
        ms = (t - base) // TICKS_PER_MS
        if len(cur) == MAX_EVENTS or ms > MAX_MS:
            raise ValueError('a held pad runs past %d events or %d ms' % (MAX_EVENTS, MAX_MS))
        cur.append((ms, h, d))
        kind, p = h >> 4, h & 15
        if kind == STRIKE:
            down.add(('strike', p))
        elif kind == RELEASE:
            down.discard(('strike', p))
        elif kind in (KEY, BRIDGE):
            (down.add if d else down.discard)(('key', kind, p))
    scripts.append(cur)
    return [check(s) for s in scripts]


def save(path, entries, threshold):
    with open(path, 'w') as fh:
        json.dump({'threshold': threshold, 'ticks_per_ms': TICKS_PER_MS,
                   'entries': [list(e) for e in entries]}, fh)


def load(path):
    with open(path) as fh:
        d = json.load(fh)
    return [tuple(e) for e in d['entries']], d.get('threshold')
