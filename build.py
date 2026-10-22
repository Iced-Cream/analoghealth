# SPDX-License-Identifier: GPL-2.0-or-later
"""Build rytmhealth's .elemod from your stock OS file.

    python build.py --stock Analog-Rytm_MKII_OS1.74.syx [--out DIR]

mod.json names each stock site by address, length and SHA-256 only
("guarded_sites"): no stock bytes are in this repository. This script reads
each site's bytes from your stock file, checks them against the hash, and
hands the sites to elekloader's SDK (elekloader.sdk.build), which builds the
mod as `python -m elekloader.sdk.build` would.

elekloader must be importable (pip install it, or set PYTHONPATH to its
folder), with the cross toolchain it needs (m68k-linux-gnu-as, -gcc, -ld;
ELEKLOADER_CROSS for another prefix).
"""
import argparse
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def sites_from_stock(mod, image, base):
    """-> the SDK's sites, each with the stock bytes read from the user's image."""
    out = []
    for s in mod['guarded_sites']:
        addr, n = int(s['addr'], 16), s['len']
        stock = image[addr - base:addr - base + n]
        if len(stock) != n or hashlib.sha256(stock).hexdigest() != s['stock_sha256']:
            raise ValueError('0x%08x: the stock file does not hold the expected code '
                             '(is it Analog Rytm MKII OS 1.74?)' % addr)
        out.append({'addr': s['addr'], 'stock': stock.hex(), 'op': s['op'], 'target': s['target']})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--stock', default=os.environ.get('ELEKLOADER_STOCK'),
                    help='the stock OS file (default: $ELEKLOADER_STOCK)')
    ap.add_argument('--out', help='where to write the .elemod (default: out/)')
    a = ap.parse_args(argv)
    if not a.stock:
        ap.error('--stock is required (or set ELEKLOADER_STOCK)')
    try:
        from elekloader import formats
        from elekloader.sdk import build as sdk
    except ImportError:
        print('BUILD FAILED: elekloader is not importable (pip install it, or set PYTHONPATH '
              'to its folder)')
        return 1
    with open(os.path.join(HERE, 'mod.json')) as fh:
        mod = json.load(fh)
    try:
        stock, dev, _rel = formats.load(a.stock)
        image = formats.main_image(stock, dev)
        sites = sites_from_stock(mod, image, dev.main_load)
        path, m = sdk.build(HERE, a.stock, a.out or os.path.join(HERE, 'out'), {'sites': sites})
    except (sdk.BuildError, ValueError, OSError, KeyError) as e:
        print('BUILD FAILED: %s' % e)
        return 1
    print('BUILT %s' % path)
    print('  %s: .run %d, .bss %d bytes; %d sites, %d relocations'
          % (m.label(), m.size('.run'), m.size('.bss'), len(m.sites), len(m.relocs)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
