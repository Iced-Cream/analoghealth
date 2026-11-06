"""Read-only memory sampling for plans: several reads in one step, some through a pointer,
and the blocks a heap's allocation bitmap gained between snapshots.

    peek-set FILE ITEM...     ITEM: ADDR:LEN, or [PTR]+OFF:LEN (the u32 at PTR, plus OFF;
                              OFF may be negative). The reads go one after another and
                              FILE gets one record per item, in order.
    peek-grown FILE A B C BASEPTR MAXBYTES
                              A, B, C: peek-set files of one item each, the same bitmap
                              (one bit per 16-byte unit, set on a block's last unit) at
                              three times. A bit clear in A and set in B and C ends a block
                              that was made after A and still lives at C. The units around
                              those bits ([BASEPTR] = the address of unit 0) are read into
                              FILE as peek-set records, merged into spans, MAXBYTES in all.

Record: u32 item number, u32 address, u32 length, u32 host time in ms since the step's
start, then the bytes; an item whose pointer leads outside the readable ranges gets a
record with length 0 and the pointer as its address, and the step goes on.
"""
import re
import struct
import time

ITEM = re.compile(r'^(?:(0x[0-9a-fA-F]+|\d+)|\[(0x[0-9a-fA-F]+|\d+)\]([+-](?:0x[0-9a-fA-F]+|\d+))?)'
                  r':(0x[0-9a-fA-F]+|\d+)$')
UNIT = 16


def parse_item(text):
    """ADDR:LEN -> (None, addr, len); [PTR]+OFF:LEN -> (ptr, off, len)."""
    m = ITEM.match(text)
    if not m:
        raise ValueError('peek-set item %r: ADDR:LEN or [PTR]+OFF:LEN' % text)
    n = int(m.group(4), 0)
    if not 0 < n <= 1 << 20:
        raise ValueError('peek-set item %r: length 1..1048576' % text)
    if m.group(1):
        return None, int(m.group(1), 0), n
    return int(m.group(2), 0), int(m.group(3) or '0', 0), n


def record(i, addr, data, ms):
    return struct.pack('>IIII', i, addr & 0xffffffff, len(data), ms) + data


def records(blob):
    """A peek-set file -> [(item, addr, ms, bytes)]."""
    out, at = [], 0
    while at < len(blob):
        i, addr, n, ms = struct.unpack_from('>IIII', blob, at)
        at += 16
        out.append((i, addr, ms, blob[at:at + n]))
        at += n
    return out


def run_set(read, readable, items):
    """read(addr, len) -> bytes; readable(addr, len) -> bool. Returns (file bytes, notes)."""
    t0 = time.monotonic()
    out, notes = [], []
    for i, (ptr, off, n) in enumerate(items):
        if ptr is None:
            addr = off
        else:
            addr = (struct.unpack('>I', read(ptr, 4))[0] + off) & 0xffffffff
        ms = int((time.monotonic() - t0) * 1000)
        if not readable(addr, n):
            out.append(record(i, addr, b'', ms))
            notes.append('item %d: 0x%08x+%d is outside the readable ranges' % (i, addr, n))
            continue
        out.append(record(i, addr, read(addr, n), ms))
    return b''.join(out), notes


def bits(data):
    return int.from_bytes(data, 'little')       # bit k of byte j = unit 8j + k


def grown_units(a, b, c):
    """Unit numbers whose bit is clear in A and set in B and C."""
    n = min(len(a), len(b), len(c))
    x = bits(c[:n]) & bits(b[:n]) & ~bits(a[:n])
    out, k = [], 0
    while x:
        low = (x & -x).bit_length() - 1        # the next set bit
        k += low
        out.append(k)
        x >>= low + 1
        k += 1
    return out


def spans(units, before=8, gap=64):
    """Block ends -> [(first unit, units)] to read: each end and `before` units ahead of it
    (a block's start is not in the bitmap), ends closer than `gap` units merged."""
    out = []
    for u in units:
        lo, hi = max(0, u - before), u + 1
        if out and lo <= out[-1][1] + gap:
            out[-1][1] = max(out[-1][1], hi)
        else:
            out.append([lo, hi])
    return [(lo, hi - lo) for lo, hi in out]


def run_grown(read, readable, a, b, c, base, maxbytes):
    """Returns (file bytes, notes, number of grown blocks)."""
    units = grown_units(a, b, c)
    t0 = time.monotonic()
    out, notes, left = [], [], maxbytes
    for i, (u, n) in enumerate(spans(units)):
        size = min(n * UNIT, left)
        if size <= 0:
            notes.append('MAXBYTES reached: %d spans not read' % (len(spans(units)) - i))
            break
        addr = base + u * UNIT
        ms = int((time.monotonic() - t0) * 1000)
        if not readable(addr, size):
            out.append(record(i, addr, b'', ms))
            notes.append('span %d: 0x%08x+%d is outside the readable ranges' % (i, addr, size))
            continue
        out.append(record(i, addr, read(addr, size), ms))
        left -= size
    return b''.join(out), notes, len(units)
