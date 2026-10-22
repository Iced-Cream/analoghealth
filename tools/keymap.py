# SPDX-License-Identifier: GPL-2.0-or-later
"""Logical names and OS key codes of the Analog Rytm MKII's buttons, from keys.def.

The panel position of each code comes from the unit's own table at run time."""
import csv
from pathlib import Path

PRODUCT = 3                   # the key protocol's product number for the Rytm MKII

KEYS = {}
MAPS = {PRODUCT: {}}
for line in (Path(__file__).resolve().parents[1] / 'keys.def').read_text().splitlines():
    if not line.startswith('KEY('):
        continue
    fields = next(csv.reader([line[4:-1]], skipinitialspace=True))
    number, name = int(fields[0]), fields[1]
    if number != len(KEYS) + 1 or name in KEYS or len(fields) != 3:
        raise ValueError('invalid key definition: %s' % line)
    KEYS[name] = number
    MAPS[PRODUCT][name] = int(fields[2], 0)          # the OS key code

SUPPORTED = {product: sum(1 << (KEYS[name] - 1) for name in mapping)
             for product, mapping in MAPS.items()}
