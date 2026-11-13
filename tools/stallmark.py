# SPDX-License-Identifier: GPL-2.0-or-later
"""elklock's stall mark, read here so no port is opened on a unit that hung on its restart.

After an OS upgrade a unit can hang on its own restart (stock A4 1.56 does, about 1 flash in
15 that it runs): the new OS is written, the unit stays on USB, and every MIDI call to it
blocks until it is power-cycled. elkflash exits 5 then, and `elklock run` marks the unit
(DEVICE.stalled.json in elklock's folder) until `elkflash usb-cycle` has seen it power-cycled.
Opening the port meanwhile would block this process (and may wedge the Windows MIDI service),
so the port code refuses instead. Read-only, stdlib only.
"""
import json
import os
import re
import sys

# unit -> what its MIDI port name contains (elklock's DEVICES); the Rytm is also reached
# through the Sidecar Pi's mirror port
NAMES = {'rytm': r'analog rytm|sidecar', 'a4': r'analog four', 'digitone': r'digitone',
         'digitakt': r'digitakt'}


def folder():
    """elklock's folder: ELKLOCK_DIR, else %LOCALAPPDATA%\\elklock (Windows) or
    $XDG_STATE_HOME/elklock, ~/.local/state/elklock."""
    if os.environ.get('ELKLOCK_DIR'):
        return os.environ['ELKLOCK_DIR']
    base = os.environ.get('LOCALAPPDATA') if sys.platform == 'win32' else \
        os.environ.get('XDG_STATE_HOME') or os.path.expanduser('~/.local/state')
    return os.path.join(base or '', 'elklock')


def stalled(unit):
    """The unit's stall record, or None."""
    try:
        with open(os.path.join(folder(), unit + '.stalled.json'), encoding='utf-8') as fh:
            return json.load(fh) or None
    except (OSError, ValueError):
        return None


def units_of(port_name):
    """The units a MIDI port name may reach."""
    name = (port_name or '').lower()
    return [u for u, rx in NAMES.items() if re.search(rx, name)]


class Stalled(RuntimeError):
    pass


def refuse_if_stalled(port_name):
    """Raises Stalled when `port_name` reaches a unit marked as stalled on its restart."""
    for u in units_of(port_name):
        rec = stalled(u)
        if rec:
            raise Stalled('the %s STALLED on its restart (%s): its MIDI port stays closed until it has been '
                          'power-cycled by hand (elkflash usb-cycle under elklock run watches that and clears '
                          'the mark); do not reflash' % (u, rec.get('why', 'after an OS upgrade')))
