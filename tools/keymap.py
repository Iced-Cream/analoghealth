# SPDX-License-Identifier: GPL-2.0-or-later
"""Logical names and OS key codes of each device's buttons, from keys-*.def, and
its encoders.

The panel position of each code comes from the unit's own table at run time."""
import csv
from pathlib import Path

RYTM, A4 = 3, 4               # the key protocol's product numbers
PRODUCTS = {RYTM: ('Analog Rytm MKII', (1, 74), 'keys-ar2.def', 'A B C D E F G H LEVEL'),
            A4: ('Analog Four MKII', (1, 56), 'keys-a4.def', 'A B C D E F G H I J LEVEL')}

KEYSETS = {}                  # product -> {name: logical ID}
MAPS = {}                     # product -> {name: OS key code}
ENCODERS = {}                 # product -> {name: encoder number, from 1}
for product, (_name, _os, path, encoders) in PRODUCTS.items():
    keys, codes = {}, {}
    for line in (Path(__file__).resolve().parents[1] / path).read_text().splitlines():
        if not line.startswith('KEY('):
            continue
        fields = next(csv.reader([line[4:-1]], skipinitialspace=True))
        number, name = int(fields[0]), fields[1]
        if number != len(keys) + 1 or name in keys or len(fields) != 3:
            raise ValueError('invalid key definition: %s' % line)
        keys[name] = number
        codes[name] = int(fields[2], 0)
    KEYSETS[product], MAPS[product] = keys, codes
    ENCODERS[product] = {n: i + 1 for i, n in enumerate(encoders.split())}

SUPPORTED = {product: sum(1 << (KEYSETS[product][name] - 1) for name in mapping)
             for product, mapping in MAPS.items()}
ALL_KEYS = set().union(*KEYSETS.values())
ALL_ENCODERS = set().union(*ENCODERS.values())
