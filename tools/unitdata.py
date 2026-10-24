# SPDX-License-Identifier: GPL-2.0-or-later
"""Small helpers for the unit's data store and kit dumps, shared by the runner
and by a local write policy (writepolicy.py)."""
import re

DATA_RENAME = 0x5E
DUMP_HEADERS = (bytes([0xF0, 0x00, 0x20, 0x3C, 0x07, 0x00]),     # Analog Rytm MKII
                bytes([0xF0, 0x00, 0x20, 0x3C, 0x06, 0x00]))     # Analog Four MKII


class DataError(Exception):
    """A data-store path, name or dump the runner cannot use."""


def split_path(path):
    """'/projects/5' -> ('/projects', 5); any '/DIR/N' path to a numbered slot."""
    m = re.fullmatch(r'(/.+)/(\d+)', path)
    if not m:
        raise DataError('expected a path to a numbered slot, /DIR/N, not %r' % path)
    return m.group(1), int(m.group(2))


def target(rpc, path):
    """-> (current name or None if the slot is empty, entry)."""
    parent, ident = split_path(path)
    for e in rpc.data_list(parent):
        if e.get('id') == ident and not e.get('dir'):
            return e['name'], e
    return None, None


def rename(rpc, path, name, timeout=5.0):
    """DataRename (0x5E): path, new name; reply u8 status (1 ok) and text."""
    args = path.encode('cp1252') + b'\0' + name.encode('cp1252') + b'\0'
    a = rpc.call(DATA_RENAME, args, timeout)
    if not a or not a[0]:
        text = a[1:].split(b'\0')[0].decode('cp1252', 'replace') if len(a) > 1 else ''
        raise DataError('rename of %s to %r refused: %s' % (path, name, text))


def kit_name(dump):
    """The name in a kit dump (stored 0x52 or edit buffer 0x58): body bytes 4-19,
    on the Rytm and the A4 alike."""
    from analogusb import unpack7
    if len(dump) < 30 or dump[:6] not in DUMP_HEADERS or dump[6] not in (0x52, 0x58):
        raise DataError('not a kit dump')
    body = unpack7(dump[10:-5])
    return body[4:20].split(b'\0')[0].decode('ascii', 'replace').rstrip()


def renamed_kit_dump(dump, name):
    """A kit dump with a new name (body bytes 4-19) and its checksum redone: the
    sum of the 7-bit data bytes, & 0x3FFF, in the two bytes after the data."""
    from analogusb import pack7, unpack7
    kit_name(dump)                              # checks it is one
    body = bytearray(unpack7(dump[10:-5]))
    body[4:20] = name.encode('ascii').ljust(16, b'\0')[:16]
    data = pack7(bytes(body))
    cs = sum(data) & 0x3FFF
    return dump[:10] + data + bytes([cs >> 7, cs & 0x7F]) + dump[-3:]


def is_kit_dump(msg):
    """A kit or sound dump the unit stores when it receives it."""
    return len(msg) > 7 and msg[:6] in DUMP_HEADERS and 0x50 <= msg[6] <= 0x5F
