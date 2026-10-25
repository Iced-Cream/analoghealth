#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Patch analoghealth's remote keys, and nothing else, into a stock OS file.

    python standalone/patch.py --stock Analog-Rytm_MKII_OS1.74.syx --out RK74.syx
    python standalone/patch.py --stock Analog-Four_MKII_OS1.56.syx --out RK56.syx

No elekloader core and no other mod: four sites and one appended block,
for tests that need remote input on firmware that otherwise is stock (see
standalone/README.md). elekloader is used only to read the stock file and
to pack and verify the output (its formats module); the code is built with
the m68k cross toolchain (inside WSL on Windows, or set CROSS).

Every site is checked against its stock bytes by SHA-256 before it is
written; the stock file is the only source of Elektron bytes. Only the main
OS changes: the output is refused unless every other section, the bootstrap
included, is the stock file's byte for byte.
"""
import argparse
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

# Per device: the build flags and the sites. Each site is (address, length,
# stock SHA-256, op, target symbol, defsym). 'jsr' and 'jmp' sites become a
# call or jump to the target; a 'call' site is a stock absolute call that
# becomes a call to the target, whose own address, read from your stock file,
# is given to the code as the defsym, for the hook to go on to.
DEVICES = {
    'analog-rytm-mk2': {
        'os': '1.74', 'version': 'RK74',
        'inc': 'ar174.inc', 'defs': [], 'keys': 'keys-ar2.def',
        'cflags': ['-DRK_OS_MINOR=74', '-DRK_PRODUCT=3'],
        'defsym': {'KEY_CONTINUE': 0x40081db4, 'KEY_STATE': 0x418d1340,
                   'UI_QUEUE': 0x41943f54, 'KEY_STACK': 76, 'KEY_TABLE': 0x40285970,
                   'ENC_INPUT': 0x40082036},
        'sites': [
            (0x400008c0, 6, 'ec49f003974d6144b6e534659eb6be29bb4edb34a26d1b4fbcf3e50f958a0748',
             'call', 'boot', 'VBR_FN'),
            (0x400a2c5a, 6, 'a5f585f812b17ff36cb7196f9bfd8db2ad57e4ae0fb696cddb4b63e6b6e6bf26',
             'call', 'tick_hook', 'TICK_FN'),
            (0x40083e04, 6, '39c04cf4e99d6fea020570d445537a5e2c1ef8983fba87f0b28684b8cadbce44',
             'jsr', 'hook_sysex', None),
            (0x40081dac, 8, '82f9c301aead4e1a90d91f8f679ba9cb91d05c1ed69dcc1c270dc59485285f1e',
             'jmp', 'core_panel_buttons', None),
        ],
    },
    'analog-four-mk2': {
        'os': '1.56', 'version': 'RK56',
        'inc': 'a4/a4156.inc', 'defs': ['A4156'], 'keys': 'keys-a4.def',
        'cflags': ['-DRK_OS_MINOR=56', '-DRK_PRODUCT=4', '-DRK_KEY_TABLE_DIRECT'],
        'defsym': {'KEY_CONTINUE': 0x40064a98, 'KEY_STATE': 0x417bf8b8,
                   'UI_QUEUE': 0x4182b850, 'KEY_STACK': 68, 'KEY_TABLE': 0x40212158,
                   'ENC_INPUT': 0x40064d10},
        'sites': [
            (0x40001180, 6, '526ee1b155acc496fd523a13e9a84ba2dd4f35004ef4a475939bc88bc486ec2a',
             'call', 'boot', 'VBR_FN'),
            (0x40098898, 6, '8138b4c06592efdd5afa864c8f96b0dc3dc264d911125c755713bab1d9d9180a',
             'call', 'tick_hook', 'TICK_FN'),
            (0x40066336, 6, 'ef21d364a38ac6499ca97c2ce94d0a693c39b63c3693e42c3c9e4094243eaf1d',
             'jsr', 'hook_sysex', None),
            (0x40064a90, 8, '3e7c95b32f444fd280cb96b2a6ea90525fe223d6e6b9352086d2cf41941ae854',
             'jmp', 'core_panel_buttons', None),
        ],
    },
}
JSR, JMP, NOP = 0x4eb9, 0x4ef9, 0x4e71
# Not remote_keys.s: its rk_now reads DTIM0, which a stock OS need not have
# running; remote.s keeps its own clock.
SOURCES = ['standalone/remote.s', 'core_candidate.s', 'core_candidate.c', 'remote_keys.c']
CFLAGS = ['-mcpu=54455', '-O2', '-ffreestanding', '-fno-builtin', '-nostdlib', '-fno-pic',
          '-fno-pie', '-fomit-frame-pointer', '-Wall', '-Werror']


class PatchError(Exception):
    pass


def sha(b):
    return hashlib.sha256(b).hexdigest()


def import_elekloader(path):
    if path:
        sys.path.insert(0, path)
    try:
        from elekloader import formats  # noqa: F401
    except ImportError:
        raise PatchError('cannot import elekloader: give --elekloader DIR (a checkout)')
    from elekloader import formats
    return formats


# ---- the toolchain ----------------------------------------------------------

def tool_runner():
    """-> (run(argv, cwd), to_tool_path) for the cross tools: native, or WSL."""
    prefix = os.environ.get('CROSS', 'm68k-linux-gnu-')
    if shutil.which(prefix + 'gcc'):
        return prefix, (lambda argv, cwd: subprocess.run(argv, cwd=cwd)), (lambda p: p)
    if os.name == 'nt' and shutil.which('wsl'):
        def to_wsl(p):
            p = os.path.abspath(p).replace('\\', '/')
            return '/mnt/%s%s' % (p[0].lower(), p[2:])

        def run(argv, cwd):
            return subprocess.run(['wsl', '--cd', to_wsl(cwd), '--'] + argv)
        return prefix, run, to_wsl
    raise PatchError('no %sgcc (install binutils-m68k-linux-gnu and gcc-m68k-linux-gnu, '
                     'in WSL on Windows)' % prefix)


def build(cfg, defsym, image_end, work):
    """Assemble, compile and link. -> (blob bytes at image_end, symbols, layout)."""
    prefix, run, path = tool_runner()
    inc_dir = os.path.dirname(os.path.join(REPO, cfg['inc']))
    syms = dict(cfg['defsym'], KEY_CLOCK=0xfc07000c, RK_KEYS=1, **defsym)
    asdefs = []
    for k, v in sorted(syms.items()):
        asdefs += ['--defsym', '%s=0x%x' % (k, v)]
    for k in cfg['defs']:
        asdefs += ['--defsym', '%s=1' % k]
    objs = []
    for src in SOURCES:
        obj = os.path.join(work, os.path.basename(src) + '.o')
        if src.endswith('.s'):
            argv = [prefix + 'as', '-mcpu=54455', '-I', path(inc_dir), '-I', path(REPO)] + asdefs \
                + ['-o', path(obj), path(os.path.join(REPO, src))]
        else:
            argv = [prefix + 'gcc'] + CFLAGS + cfg['cflags'] + ['-I', path(REPO), '-c', '-o',
                                                                path(obj),
                                                                path(os.path.join(REPO, src))]
        if run(argv, REPO).returncode:
            raise PatchError('building %s failed' % src)
        objs.append(obj)
    lo, hi = 0x47BE0000, 0x47C00000
    ld = os.path.join(work, 'remote.ld')
    with open(ld, 'w') as f:
        f.write('''ENTRY(boot)
SECTIONS
{
  .boot 0x%08x : { *(.boot) . = ALIGN(4); }
  .run 0x%08x : AT(LOADADDR(.boot) + SIZEOF(.boot))
  { __run_start = .; *(.run) *(.text .text.*) *(.rodata .rodata.*) *(.data .data.*) . = ALIGN(4); __run_end = .; }
  __run_load = LOADADDR(.run);
  __run_words = (__run_end - __run_start) / 4;
  .bss (NOLOAD) : { __bss_start = .; *(.bss .bss.*) *(COMMON) . = ALIGN(4); __bss_end = .; }
  __bss_words = (__bss_end - __bss_start) / 4;
  /DISCARD/ : { *(.comment) *(.note*) *(.eh_frame*) }
}
''' % (image_end, lo))
    elf = os.path.join(work, 'remote.elf')
    if run([prefix + 'ld', '-z', 'noexecstack', '--no-warn-rwx-segments', '-T', path(ld),
            '-o', path(elf)] + [path(o) for o in objs]
           + ['-Map', path(os.path.join(work, 'remote.map'))], REPO).returncode:
        raise PatchError('linking failed')
    binf = os.path.join(work, 'remote.bin')
    if run([prefix + 'objcopy', '-O', 'binary', '-j', '.boot', '-j', '.run', path(elf),
            path(binf)], REPO).returncode:
        raise PatchError('objcopy failed')
    nm = os.path.join(work, 'remote.nm')
    with open(nm, 'w') as out:
        argv = [prefix + 'nm', path(elf)]
        if os.name == 'nt' and not shutil.which(prefix + 'nm'):
            argv = ['wsl', '--cd', path(REPO), '--'] + argv
        if subprocess.run(argv, stdout=out).returncode:
            raise PatchError('nm failed')
    symbols = {}
    with open(nm) as f:
        for line in f:
            parts = line.split()
            if len(parts) == 3:
                symbols[parts[2]] = int(parts[0], 16)
    with open(binf, 'rb') as f:
        blob = f.read()
    for s in ('boot', 'tick_hook', 'hook_sysex', 'core_panel_buttons', '__run_start',
              '__run_end', '__bss_start', '__bss_end', '__run_load'):
        if s not in symbols:
            raise PatchError('the link has no %s' % s)
    if symbols['boot'] != image_end or symbols['__run_load'] - image_end + \
            symbols['__run_end'] - symbols['__run_start'] != len(blob):
        raise PatchError('unexpected layout: boot 0x%x, %d bytes' % (symbols['boot'], len(blob)))
    if symbols['__bss_end'] > hi:
        raise PatchError('the code and its RAM end at 0x%x, past the mods\' area (0x%x)'
                         % (symbols['__bss_end'], hi))
    layout = {'boot': [image_end, symbols['__run_load']],
              'run': [symbols['__run_start'], symbols['__run_end']],
              'bss': [symbols['__bss_start'], symbols['__bss_end']],
              'area': [lo, hi], 'blob_len': len(blob)}
    return blob, symbols, layout


# ---- the patch -------------------------------------------------------------

def patch(stock_path, out_path, version, elekloader, log=print):
    formats = import_elekloader(elekloader)
    stock, dev, rel = formats.load(stock_path)
    cfg = DEVICES.get(dev.key)
    if cfg is None or rel.version != cfg['os']:
        raise PatchError('%s %s is not supported (Analog Rytm MKII 1.74, Analog Four MKII 1.56)'
                         % (dev.name, rel.version))
    img0 = bytes(formats.main_image(stock, dev))
    if sha(img0) != rel.main_sha256:
        raise PatchError('its main OS is not the known %s %s image' % (dev.name, rel.version))
    log('stock: %s OS %s (%s)' % (dev.name, rel.version, stock.sha256))
    base = dev.main_load
    image_end = base + len(img0)

    def at(addr, n):
        return img0[addr - base:addr - base + n]

    # Check every site, and read the stock call targets the hooks go on to.
    defsym = {}
    for addr, n, want, op, target, keep in cfg['sites']:
        got = at(addr, n)
        if sha(got) != want:
            raise PatchError('0x%08x: the stock bytes are not as expected' % addr)
        if op == 'call':                    # an absolute call: the hook goes on to its target
            defsym[keep] = struct.unpack('>I', got[2:6])[0]
    work = tempfile.mkdtemp(prefix='analogremote-', dir=os.path.join(REPO, 'out')
                            if os.path.isdir(os.path.join(REPO, 'out')) else None)
    try:
        blob, symbols, layout = build(cfg, defsym, image_end, work)
    finally:
        if not os.environ.get('KEEP_WORK'):
            shutil.rmtree(work, ignore_errors=True)
        else:
            log('build files kept in %s' % work)
    img = bytearray(img0) + blob
    sites = []
    for addr, n, want, op, target, keep in cfg['sites']:
        new = struct.pack('>HI', JMP if op == 'jmp' else JSR, symbols[target])
        new += struct.pack('>H', NOP) * ((n - len(new)) // 2)
        assert len(new) == n
        img[addr - base:addr - base + n] = new
        sites.append({'addr': '0x%08x' % addr, 'len': n, 'stock_sha256': sha(at(addr, n)),
                      'new': new.hex(), 'target': target})
    # The only changes: the sites and the appended block.
    changed = [i for i in range(len(img0)) if img[i] != img0[i]]
    allowed = set()
    for addr, n, *_ in cfg['sites']:
        allowed.update(range(addr - base, addr - base + n))
    if not set(changed) <= allowed:
        raise PatchError('changes outside the sites')
    log('sites: %s' % ', '.join('%s %s' % (s['addr'], s['target']) for s in sites))
    log('code: %d bytes appended at 0x%08x; runs at 0x%08x-0x%08x, RAM to 0x%08x (area 0x%08x-0x%08x)'
        % (len(blob), image_end, layout['run'][0], layout['run'][1], layout['bss'][1],
           layout['area'][0], layout['area'][1]))
    version = version or cfg['version']
    formats.check_version(dev, version)
    stored = formats.pack_main(bytes(img), dev)
    outputs = formats.write(stock, stored, dev, version)
    facts = formats.verify(outputs, stock, bytes(img), dev, version)
    stock_sections = {s for s, v in facts['sections'].items() if v['stock']}
    others = {s for s in facts['sections'] if str(s) != str(dev.main_section)}
    if stock_sections != others:
        raise PatchError('sections %s are not stock' % sorted(others - stock_sections))
    if int(facts['flash_end'], 16) > dev.flash_limit:
        raise PatchError('the file does not fit in flash')
    log('verified: %s; flash ends %s' % ('; '.join(facts['untouched']), facts['flash_end']))
    syx = outputs['syx']
    with open(out_path, 'wb') as f:
        f.write(syx)
    man = {'patcher': 'analoghealth standalone/patch.py', 'name': 'analogremote 1.0',
           'device': dev.key, 'os': rel.version,
           'stock': {'path': os.path.abspath(stock_path), 'sha256': stock.sha256,
                     'main_sha256': sha(img0)},
           'version': version, 'sites': sites, 'layout': layout,
           'main_sha256': sha(bytes(img)), 'main_len': len(img), 'output': facts,
           'sha256': sha(syx)}
    with open(out_path + '.json', 'w') as f:
        json.dump(man, f, indent=1)
    log('WROTE %s\n  sha256 %s, version %s, %d bytes' % (out_path, sha(syx), version, len(syx)))
    return man


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--stock', required=True, help='the stock OS file (.syx)')
    ap.add_argument('--out', required=True, help='the patched OS file to write')
    ap.add_argument('--version', help='the 4-character version the unit shows '
                    '(default RK74 / RK56)')
    ap.add_argument('--elekloader', default=os.environ.get('ELEKLOADER'),
                    help='an elekloader checkout, if it is not importable')
    a = ap.parse_args(argv)
    try:
        patch(a.stock, a.out, a.version, a.elekloader)
    except PatchError as e:
        sys.exit('patch.py: %s' % e)


if __name__ == '__main__':
    main()
