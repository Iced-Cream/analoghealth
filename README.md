# rytmhealth

A diagnostics mod for the Elektron Analog Rytm MKII, OS 1.74: irpina's
[digihealth](https://github.com/irpina/digihealth) for the Digitakt mk1,
ported to the Rytm. It adds two rows to SETTINGS:

- **SYSTEM INFO** shows CPU, DSP, RAM and free sample memory in the top bar.
  A read-only USB diagnostics channel lets `tools/rytmusb.py` read the same
  figures, and more, from a computer.
- **REMOTE KEYS** (on at every power-on) lets `tools/rytmusb.py keys` press
  the panel's buttons, and `tools/rytmusb.py turn` turn its encoders, over
  USB: see [Remote keys](#remote-keys). Off, the
  unit refuses key commands and lets go of any key the computer holds; the
  diagnostics still answer.

digihealth's third row, **FAST AUDIO**, is not ported: see
[Why no FAST AUDIO](#why-no-fast-audio).

It is an [elekloader](https://github.com/irpina/elekloader) mod and needs
core-ar2 (built and checked with core-ar2 0.4). elekloader builds a custom OS file on your
own machine, from your stock OS file and the mods you pick; nothing from
Elektron is distributed.

**Status:** passed on a unit (OS 1.74, 22 Oct 2026), and checked in the Rytm
emulator and on the host (see [How it was checked](#how-it-was-checked)).

This repository holds no Elektron code or data: stock code sites are named by
address, length and SHA-256, and their bytes, like the panel's button layout,
come from your own stock file or your own unit.

## SYSTEM INFO

- **The top bar** shows two pages, two seconds each:
  - CPU and DSP load (DSP now, and its peak in the last second);
  - free RAM (the heap) and free sample memory.
- **DSP** is the ColdFire's audio interrupt: the sample voices, the effects,
  the mix. The Rytm's drum voices are analog and cost it nothing, so they are
  not in it.
- **CPU** is the share of time the processor is not idle.
- **The USB channel** is SysEx with Elektron's manufacturer header and a
  device byte (0x7D) that no Elektron machine uses, so a stock OS ignores it.
  Its diagnostics commands are read-only: HELLO, STATS (the once-a-second
  snapshot: render and idle time, the heap by block size, free sample memory)
  and PEEK (DDR and the SRAM only). The diagnostics protocol is
  digihealth's, byte for byte.

```text
python tools/rytmusb.py cfw          # which mod answers, and its uptime
python tools/rytmusb.py stats 10     # ten readings, one a second
python tools/rytmusb.py peek 0x47be0000 64   # the mods' RAM area
```

`rytmusb.py` runs on Windows (winmm, through ctypes; no packages) and on
macOS/Linux (Mido + python-rtmidi: `pip install mido python-rtmidi`). Close
Elektron Transfer and Overbridge apps first on Windows, which lets one
program at a time open a MIDI port. The client expects one MIDI input and
output whose name contains "Rytm"; it refuses ambiguous matches.

## Remote keys

All 78 keys of the panel: the 16 trig keys, the 12 pads, the 8 bank keys, the
encoder pushes and every other button. Names are the OS's own (`FUNC`, `TRK`,
`RTRG`, `GLOBAL`, `PLAYMODE`, `SCNE`, `CHRO`, ...): see [the complete button
map](docs/BUTTONS.md).

```bash
python tools/rytmusb.py keys GLOBAL                     # open SETTINGS
python tools/rytmusb.py keys 'FUNC(PLAY)'               # hold FUNC, tap PLAY
python tools/rytmusb.py keys 'GLOBAL,DOWN,DOWN,YES'
```

A bare name taps that button; `FUNC(PLAY)` holds FUNC, taps PLAY, then
releases FUNC before the next step. Commas separate steps; quote the sequence
so the shell keeps the parentheses. `--tap-ms` defaults to 100 (maximum 250)
and `--hold-ms` to 2000 (maximum 2000); the hold is a safety lease, not a
delay. All names are checked before any key action, each step waits for
completion, and an error stops the sequence and lets go of every key.
Completion means the stock button driver took the transition, not that a
menu opened.

- **Pads** from the computer are their key events only: the sound is a
  separate velocity message the helper does not send.
- **Encoders:** `python tools/rytmusb.py turn E -12` turns encoder E (A-H
  are the data encoders, LEVEL the level/data knob) 12 counts left, one count
  per UI tick (20 a second), as a panel turn does: the value popup and the
  header's long name appear as for a hand turn. As on the panel, several
  counts make one value step (on a unit, 60 counts at 4 a tick moved FRQ 12
  steps, and 20 counts at 2 a tick moved the track level 2), so a turn of a
  few counts may change nothing. The OS also drops the first counts of a turn
  after a pause, so a small turn out and the same turn back need not return
  to the start: in the emulator, panel turns of +10 and then -10 counts on
  the SRC page moved SWT and left it there, as remote turns do on a unit. `--step N` sends N counts a tick (faster,
  and the OS's acceleration applies); `--hold FUNC` holds a key through the
  turn. An encoder pushed within the last moment ignores turns,
  as on the panel.
- **Physical keys win:** a key held on the panel cannot be taken by the
  computer, and a panel press of a key the computer holds survives the
  computer's release.
- **Watching the panel:** `python tools/rytmusb.py watch 30` prints keys as
  they go down and up for 30 seconds, and `watch --until FUNC` waits for a
  key. It reads the button state the OS keeps with PEEK about 20 times a
  second, so it needs nothing more from the firmware; it sees keys the
  computer holds too, and a tap shorter than one read can be missed.

Python callers can reuse any port with `send`, `receive` and `close`:

```python
from rytmusb import Diag, RemoteKeys  # tools/ on the import path

with RemoteKeys(Diag(port)) as keys:
    keys.press('FUNC')
    keys.tap('PLAY')
    keys.release('FUNC')
```

The protocol is digihealth's revision 2 with 128-bit key maps, because the
Rytm has more keys than revision 2's 64-bit maps hold, and a TURN action for
the encoders (revision 4). See [the
protocol reference](docs/REMOTE-KEYS.md).

## Why no FAST AUDIO

digihealth's FAST AUDIO runs the Digitakt's audio code from a stretch of the
processor's 64 KB on-chip SRAM that the Digitakt's OS leaves empty. The Rytm's
OS fills and uses all of that SRAM itself, so there is no room to run code
from without taking room the OS needs.

## Install

You need:
- **elekloader** with the Rytm profile and core-ar2 (see
  [elekloader](https://github.com/irpina/elekloader)); from source, build the
  core with `python -m elekloader.sdk.build mods/core-ar2 --stock
  Analog-Rytm_MKII_OS1.74.syx`.
- **This mod**, built from source (below).
- **The stock OS file** `Analog-Rytm_MKII_OS1.74.syx`, from Elektron's Analog
  Rytm MKII downloads. elekloader recognises it by its hash.

```bash
python -m elekloader.patch --stock Analog-Rytm_MKII_OS1.74.syx \
    --mod core-0.4.elemod --mod out/rytmhealth-1.1.elemod \
    --out Analog-Rytm_MKII_OS1.74-health.syx --version RH12
```

Flash it with Elektron Transfer, as for any OS update, and don't turn the
unit off until the upgrade is done. **Recovery:** elekloader never changes
the bootloader, so the stock OS file always restores the unit (hold FUNC
while powering on for the startup menu, then OS UPGRADE).

## Build it from source

The Digitakt mk1 cross toolchain (m68k binutils and gcc; on Debian or Ubuntu,
`apt install binutils-m68k-linux-gnu gcc-m68k-linux-gnu`; on Windows, inside
WSL) and elekloader, importable:

```bash
python build.py --stock Analog-Rytm_MKII_OS1.74.syx     # -> out/rytmhealth-1.1.elemod
python -m elekloader.lint out/rytmhealth-1.1.elemod --stock Analog-Rytm_MKII_OS1.74.syx --with core-0.4.elemod
```

| file | |
|---|---|
| `mod.json` | the mod: its sites (by address, length and SHA-256), handlers and resources |
| `build.py` | reads each site's stock bytes from your stock file, checks the hash, and calls elekloader's SDK |
| `sysinfo.s` | the SYSTEM INFO and REMOTE KEYS rows, the readout, the render and idle timing, the USB channel |
| `ar174.inc` | the stock routines and data it uses, OS 1.74 |
| `core_candidate.c`, `core_candidate.s`, `core_candidate.h` | the physical/synthetic input bridge on the button driver, private to this mod |
| `remote_keys.c`, `remote_keys.s`, `remote_keys.h` | the bounded key mailbox, UI tick and clock |
| `keys.def`, `tools/keymap.py` | key names and their OS key codes; the firmware finds each key on the panel from the unit's own table |
| `tools/rytmusb.py`, `tools/winmidi.py`, `tools/midimido.py` | the diagnostics and key client, and its MIDI transports |
| `tests/test_keys.py`, `tests/fixtures/remote_keys_host.c` | the host tests |

### How the port was made

digihealth's stock routines, found again in the Rytm's OS 1.74,
with the Rytm's addresses (`ar174.inc`). It hooks the same three
places as digihealth: the task switch (idle time), the SysEx input (the USB
channel) and the panel's button input (the remote keys).

## How it was checked

- **Build:** `elekloader.sdk.build` and `elekloader.lint` against stock 1.74
  with core-ar2 0.4: no overlaps, stock bytes as expected, code sites whole
  instructions; `elekloader.patch` builds and verifies the OS (RH11, sha256
  `f17a9b9fe3487b5e5cff971bb7f671287e6589f2764615c368ac63be505eeccf`).
- **Host tests** (`tests/test_keys.py`, 12): the real C protocol and bridge
  behind a fake port, with a synthetic panel table: capabilities, a hold and
  tap, every one of the 78 keys found and reaching its place, a key missing
  from the unit's table not offered, the CLI sequence, physical overlap, a
  busy queue, the REMOTE KEYS row off, the hold lease, name checks, and the
  client's selftest. With `ELEKLOADER_STOCK` set to the stock file, it checks
  that the unit's table holds every key code in `keys.def`.
- **The Rytm emulator** ([trigcondition/analog-emu](https://github.com/trigcondition/analog-emu)),
  from a cold boot (RH10, and RH11 for the remote keys and the readout):
  - HELLO, STATS and CAPABILITIES sent on USB MIDI were answered:
    "rytmhealth 1.0", 1500 renders a second at 132.000 MHz, CPU 51 % and DSP
    49 % at rest, 13.26 MB heap free, 64.00 MB sample memory free; 78 keys,
    revision 3. The heap walk stopped one free list at a failed check, which
    digihealth also sees on a stock Digitakt (its README).
  - Remote GLOBAL opened SETTINGS; eight remote DOWNs reached SYSTEM INFO,
    after SYSTEM; YES ticked it; REMOTE KEYS showed ticked at power-on; NO
    closed the menu and the top bar showed "CPU 51% DSP 49%/49%", then
    "RAM 13.2M SMP 64.0M".
  - A remote BD tap sent no MIDI note (a pad hit does).
  - The emulator stands in for the OS's SysEx sending, so the unit is the
    check for the USB replies.
- **On a unit** (Analog Rytm MKII, OS 1.74, RH10 = core-ar2 0.4 + rytmhealth
  1.0 before its key positions moved to the unit's own table, 22 Oct 2026):
  every step of the hardware test passed. It boots and
  reports RH10; the SYSTEM INFO and REMOTE KEYS rows appear, and the readout's
  two pages show stopped and playing; `rytmusb.py cfw` and `stats` answer over
  USB; remote GLOBAL,
  the SETTINGS sequence, `FUNC(PLAY)`, PLAY and STOP work; REMOTE KEYS off
  refuses keys while diagnostics answer; a panel-held FUNC is refused as an
  overlap; a remote pad is silent; normal playing is unchanged; after a power
  cycle SYSTEM INFO is off and REMOTE KEYS on.
- **RH11 on the unit** (23 Oct 2026, flashed over USB): it reports RH11;
  `cfw` and `stats` answer; GLOBAL, NO, the SETTINGS sequence, `FUNC(PLAY)`,
  PLAY and STOP complete; screen captures show both SETTINGS rows and the
  readout ("CPU 69% DSP 64%/71%"); with FUNC held on the panel, `watch` saw
  FUNC down and `keys 'FUNC(PLAY)'` was refused as a physical overlap.
- **RH12 on the unit** (rytmhealth 1.1, 23 Oct 2026): on the FILTER page a
  remote `turn E -60 --step 4` put FREQUENCY in the header and moved FRQ from
  127 to 115, and `turn E 60 --step 4` brought it back; `turn LEVEL -20
  --step 2` showed TRK LEVEL 98, and `+20` brought it back to 100.

## Credits

- [digihealth](https://github.com/irpina/digihealth) by irpina
  (GPL-2.0-or-later): this mod is its SYSTEM INFO and REMOTE KEYS, with the
  Rytm's addresses; the protocol, the client and the input bridge are its.
- [elekloader](https://github.com/irpina/elekloader) by irpina
  (GPL-2.0-or-later) and its core-ar2.
- [analog-emu](https://github.com/trigcondition/analog-emu) by trigcondition
  (MIT): the emulator the checks ran in.

## Licence

GPL-2.0-or-later. rytmhealth is free software: you can redistribute it and/or
modify it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 2 of the License, or (at your
option) any later version. It is distributed in the hope that it will be
useful, but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General
Public License for more details: [LICENSE](LICENSE) holds version 2.

Not affiliated with Elektron. Analog Rytm and Digitakt are trademarks of
Elektron. Custom firmware is at your own risk.
