# SPDX-License-Identifier: GPL-2.0-or-later
"""Short MIDI messages (notes, CCs, NRPNs) for `capture FILE S short`: parsing a raw
byte stream, decoding NRPNs and the matchers a capture step asserts with.

Ports keep these only when opened with short=True, and leave out real-time bytes
(clock F8, start/stop, active sensing FE): a unit sending clock would bury the rest.
Opened with realtime=True as well, they keep the real-time bytes too, each as a one-byte
message; a capture step asks for them (`realtime`, `realtime=noclock`, `rt=XX`) and
keeps only what it asked for (filter_realtime).
"""
import time


def data_len(status):
    """How many data bytes follow `status` (None: not a short-message status)."""
    if 0x80 <= status < 0xF0:
        return 1 if 0xC0 <= status < 0xE0 else 2
    return {0xF1: 1, 0xF2: 2, 0xF3: 1, 0xF6: 0}.get(status)


REALTIME = {0xF8: 'clock', 0xF9: 'real time F9', 0xFA: 'start', 0xFB: 'continue', 0xFC: 'stop',
            0xFD: 'real time FD', 0xFE: 'active sensing', 0xFF: 'reset'}


def from_word(word, realtime=False):
    """A winmm MIM_DATA dwParam1 -> the message bytes, or None for junk (and for real time
    unless `realtime`)."""
    status = word & 0xFF
    if status >= 0xF8:
        return bytes([status]) if realtime else None
    n = data_len(status)
    if n is None:
        return None
    return bytes((word >> (8 * k)) & 0xFF for k in range(n + 1))


class Parser:
    """A raw MIDI byte stream (outside SysEx) -> whole short messages, running status
    expanded. Feed it the bytes a SysEx reader does not take."""

    def __init__(self, realtime=False):
        self.status, self.buf = None, []
        self.realtime = realtime

    def feed(self, b):
        """One byte -> a whole message (bytes) when it completes one, else None."""
        if b >= 0xF8:                          # real time: kept only if asked, never breaks a message
            return bytes([b]) if self.realtime else None
        if b & 0x80:
            n = data_len(b)
            if n is None:                      # F0/F7/F4/F5: ends any running status
                self.status, self.buf = None, []
                return None
            self.status = b if b < 0xF0 else None   # system common cancels running status
            self.buf = [b]
            if n == 0:
                self.buf = []
                return bytes([b])
            return None
        if not self.buf:
            if self.status is None:            # a stray data byte
                return None
            self.buf = [self.status]
        self.buf.append(b)
        if len(self.buf) - 1 == data_len(self.buf[0]):
            msg, self.buf = bytes(self.buf), []
            return msg
        return None


def stamp(msg):
    return (time.monotonic(), msg)


def nrpns(msgs):
    """Captured short messages -> [(channel 1-16, msb, lsb, value, has_lsb)], one per data
    entry. CC 99/98 pick the parameter on that channel, CC 6 sends the value's top 7 bits
    and a CC 38 right after it the low 7 bits: value is (CC6 << 7) | CC38 then, else CC6."""
    sel, out = {}, []
    last = {}                                   # channel -> index in out of the CC 6 to extend
    for m in msgs:
        if len(m) != 3 or m[0] & 0xF0 != 0xB0:
            continue
        ch, num, v = (m[0] & 0x0F) + 1, m[1], m[2]
        msb, lsb = sel.get(ch, (None, None))
        if num == 99:
            sel[ch] = (v, lsb)
            last.pop(ch, None)
        elif num == 98:
            sel[ch] = (msb, v)
            last.pop(ch, None)
        elif num == 6 and msb is not None and lsb is not None:
            out.append((ch, msb, lsb, v, False))
            last[ch] = len(out) - 1
        elif num == 38 and ch in last:
            i = last.pop(ch)
            c, a, b, hi, _ = out[i]
            out[i] = (c, a, b, (hi << 7) | v, True)
        else:
            last.pop(ch, None)
    return out


def describe(m):
    if len(m) == 1 and m[0] >= 0xF8:
        return REALTIME[m[0]]
    kind = m[0] & 0xF0
    ch = (m[0] & 0x0F) + 1
    if kind == 0xB0:
        return 'cc ch %d %d = %d' % (ch, m[1], m[2])
    if kind == 0x90:
        return 'note on ch %d %d vel %d' % (ch, m[1], m[2])
    if kind == 0x80:
        return 'note off ch %d %d' % (ch, m[1])
    if kind == 0xC0:
        return 'program ch %d %d' % (ch, m[1])
    return ''


def parse_matcher(word):
    """`nrpn=CH:MSB:LSB[:VAL]`, `cc=CH:NUM[:VAL]` or `rt=XX` (a real-time byte in hex, F8-FF)
    -> (kind, numbers), or None when the word is none of them."""
    kind, eq, spec = word.partition('=')
    kind = kind.lower()
    if not eq or kind not in ('nrpn', 'cc', 'rt'):
        return None
    if kind == 'rt':
        try:
            b = int(spec, 16)
        except ValueError:
            b = -1
        if not 0xF8 <= b <= 0xFF:
            raise ValueError('%s: give rt=XX, a real-time byte F8-FF in hex (FA start, '
                             'FB continue, FC stop)' % word)
        return kind, (b,)
    try:
        nums = [int(x, 0) for x in spec.split(':')]
    except ValueError:
        nums = []
    lo = 3 if kind == 'nrpn' else 2
    if not lo <= len(nums) <= lo + 1 or not 1 <= nums[0] <= 16:
        raise ValueError('%s: give %s' % (word, 'nrpn=CH:MSB:LSB[:VAL]' if kind == 'nrpn'
                                          else 'cc=CH:NUM[:VAL]'))
    return kind, tuple(nums)


def matches(matcher, msgs):
    """Whether `msgs` (short messages) hold one that `matcher` (parse_matcher) asks for."""
    kind, want = matcher
    if kind == 'rt':
        return bytes(want) in msgs
    if kind == 'nrpn':
        return any((c, a, b) == want[:3] and (len(want) == 3 or v == want[3])
                   for c, a, b, v, _ in nrpns(msgs))
    ch, num = want[0], want[1]
    return any(len(m) == 3 and m[0] == 0xB0 | (ch - 1) and m[1] == num
               and (len(want) == 2 or m[2] == want[2]) for m in msgs)


def realtime_mode(words, matchers=()):
    """A capture step's words -> which real-time bytes it keeps: None (none, as always),
    'all' (`realtime`, or an rt= matcher) or 'noclock' (`realtime=noclock`: all but clock F8)."""
    mode = None
    for w in words:
        lw = w.lower()
        if lw == 'realtime':
            mode = mode or 'all'
        elif lw == 'realtime=noclock':
            mode = 'noclock'
        elif lw.startswith('realtime='):
            raise ValueError('%s: give realtime or realtime=noclock' % w)
    if mode is None and any(k == 'rt' for k, _ in matchers):
        mode = 'all'
    if mode == 'noclock' and ('rt', (0xF8,)) in matchers:
        raise ValueError('rt=F8 asks for clock, which realtime=noclock drops')
    return mode


def filter_realtime(stamped, mode):
    """Stamped messages -> only the real-time ones `mode` (realtime_mode) keeps; the rest stay."""
    if mode == 'all':
        return list(stamped)
    drop = {0xF8} if mode == 'noclock' else set(REALTIME)
    return [(t, m) for t, m in stamped if not (len(m) == 1 and m[0] in drop)]


def text(stamped, since=None):
    """The capture file: one message per line (seconds from the step's start, hex bytes,
    what it is), then the NRPNs they carry."""
    t0 = since if since is not None else (stamped[0][0] if stamped else 0.0)
    lines = ['# short MIDI messages: seconds, bytes, meaning']
    for t, m in stamped:
        lines.append(('%8.3f  %-9s %s' % (t - t0, m.hex(' ').upper(), describe(m))).rstrip())
    found = nrpns([m for _, m in stamped])
    if found:
        lines.append('# NRPNs (channel msb:lsb = value; 14-bit when a CC 38 followed the CC 6)')
        for c, a, b, v, full in found:
            lines.append('nrpn ch %d %d:%d = %d%s' % (c, a, b, v, '' if full else ' (CC 6 only)'))
    return '\n'.join(lines) + '\n'
