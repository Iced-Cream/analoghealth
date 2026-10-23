# SPDX-License-Identifier: GPL-2.0-or-later
"""Rules for automated test sessions that write to a unit.

Everything a test session creates or changes is named with the prefix `~T `.
A session may write, overwrite, rename or clear only an item whose current
name starts with `~T`, or an empty slot, and it gives an empty slot a `~T`
name in the same step. Project slots 121-128 are reserved for tests; outside
them only items already named `~T` may be touched. A kit or sound dump sent
to the unit lands in the active project, so the active project must be a
test project first: by convention a test project names its kit 1 `~T ...`,
and the active project's kit 1 is read back (a stored-kit dump request
answers from the active project) before any kit write.

The rules are for shared test rigs, not for everyone: they apply on a
computer where the file `test-session-rules` exists in the analoghealth
config folder (%LOCALAPPDATA%\\analoghealth on Windows, ~/.config/analoghealth
elsewhere). Where it exists there is deliberately no option to switch them
off; elsewhere the runner writes wherever it is told.

Names follow the unit's name editor: up to 16 characters (15 here, to leave
a terminator), upper case, from A-Z 0-9 space and + - = & / # @ ? % $ ~.
"""
import os
import re
import struct
import sys

PREFIX = '~T '
NAME_CHARS = set('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 +-=&/#@?%$~')
NAME_MAX = 15
TEST_PROJECTS = range(121, 129)
DATA_RENAME = 0x5E


class RuleError(Exception):
    """A write the test-session rules do not allow."""


def marker_path():
    base = os.environ.get('LOCALAPPDATA') if sys.platform == 'win32' else None
    base = base or os.path.join(os.path.expanduser('~'), '.config')
    return os.path.join(base, 'analoghealth', 'test-session-rules')


def enforced():
    """True on a computer set up as a shared test rig (the marker file exists)."""
    return os.path.exists(marker_path())


def is_test_name(name):
    return bool(name) and name.upper().startswith(PREFIX.rstrip())


def check_new_name(name):
    """-> the name as the unit will store it, or RuleError."""
    name = name.upper()
    if not name.startswith(PREFIX):
        raise RuleError('a test item\'s name must start with %r: %r' % (PREFIX, name))
    bad = sorted(set(name) - NAME_CHARS)
    if bad:
        raise RuleError('%r has characters the unit drops: %s' % (name, ''.join(bad)))
    if len(name) > NAME_MAX or len(name) <= len(PREFIX):
        raise RuleError('%r: use %d to %d characters' % (name, len(PREFIX) + 1, NAME_MAX))
    return name


def split_path(path):
    """'/projects/121' -> ('/projects', 121); '/soundbanks/A/5' -> ('/soundbanks/A', 5).
    Off a test rig any '/DIR/N' path is accepted."""
    m = re.fullmatch(r'(/projects|/soundbanks/[A-P])/(\d+)', path)
    if not m and not enforced():
        m = re.fullmatch(r'(/.+)/(\d+)', path)
        if not m:
            raise RuleError('expected a path to a numbered slot, /DIR/N, not %r' % path)
    if not m:
        raise RuleError('test sessions may write only /projects/N and /soundbanks/X/N, not %r' % path)
    return m.group(1), int(m.group(2))


def target(rpc, path):
    """-> (current name or None if the slot is empty, entry) for a writable path."""
    parent, ident = split_path(path)
    for e in rpc.data_list(parent):
        if e.get('id') == ident and not e.get('dir'):
            return e['name'], e
    return None, None


def check_write(rpc, path, new_name=None):
    """Before a write, clear or rename of `path`: -> the ~T name the item must
    carry afterwards. An empty slot needs `new_name`; a ~T item keeps its name
    unless `new_name` is given. Off a test rig: -> `new_name` upper-cased, or None
    (no rename)."""
    if not enforced():
        return new_name.upper() if new_name else None
    parent, ident = split_path(path)
    name, _ = target(rpc, path)
    if parent == '/projects' and ident not in TEST_PROJECTS and name is None:
        raise RuleError('project slot %d is empty but outside the test slots 121-128' % ident)
    if name is not None and not is_test_name(name):
        raise RuleError('%s holds %r, which is not a test item (%r prefix): refused'
                        % (path, name, PREFIX))
    if new_name is not None:
        return check_new_name(new_name)
    if name is None:
        raise RuleError('%s is empty: give the new item a %r name (--name)' % (path, PREFIX))
    return name


def rename(rpc, path, name, timeout=5.0):
    """DataRename (0x5E): path, new name; reply u8 status (1 ok) and text."""
    args = path.encode('cp1252') + b'\0' + name.encode('cp1252') + b'\0'
    a = rpc.call(DATA_RENAME, args, timeout)
    if not a or not a[0]:
        text = a[1:].split(b'\0')[0].decode('cp1252', 'replace') if len(a) > 1 else ''
        raise RuleError('rename of %s to %r refused: %s' % (path, name, text))


def kit_name(dump):
    """The name in a kit dump (stored 0x52 or edit buffer 0x58): body bytes 4-19."""
    from analogusb import unpack7
    if len(dump) < 30 or dump[:6] != bytes([0xF0, 0x00, 0x20, 0x3C, 0x07, 0x00]):
        raise RuleError('not a kit dump')
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


def check_active_project(session):
    """The active project must be a test project: its kit 1 is named ~T.
    Off a test rig: -> None, nothing checked."""
    if not enforced():
        return None
    from analogrun import KIT_REQUEST, dump_request   # the runner's dump request
    try:
        msgs = session.capture(request=dump_request(KIT_REQUEST, 0), wait=5.0, quiet=0.5)
    except TimeoutError:
        raise RuleError('the active project\'s kit 1 did not answer: cannot tell it is a test project')
    name = kit_name(msgs[0])
    if not is_test_name(name):
        raise RuleError('the active project\'s kit 1 is %r, not a %r kit: load a test project '
                        '(slots 121-128) first' % (name, PREFIX))
    return name


def is_kit_dump(msg):
    """A kit or sound dump the unit stores when it receives it."""
    return (len(msg) > 7 and msg[:6] == bytes([0xF0, 0x00, 0x20, 0x3C, 0x07, 0x00])
            and 0x50 <= msg[6] <= 0x5F)
