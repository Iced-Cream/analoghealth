# analogremote: remote keys on an otherwise stock OS

`standalone/patch.py` patches analoghealth's remote keys, and nothing else,
into a stock OS file: Analog Rytm MKII 1.74 or Analog Four MKII 1.56. It is
for tests that need keys and encoder turns from a computer, on firmware that
is as close to stock as remote input allows.

It does not use an elekloader core or any other mod. elekloader is used only
to read the stock file and to pack and verify the output.

```bash
python standalone/patch.py --stock Analog-Rytm_MKII_OS1.74.syx --out RK74.syx
python standalone/patch.py --stock Analog-Four_MKII_OS1.56.syx --out RK56.syx
```

The unit shows version RK74 or RK56. Give `--version` for another
4-character name and `--elekloader DIR` if elekloader is not importable. The
build needs the m68k cross toolchain, as for analoghealth: native, inside WSL
on Windows, or a prefix set in `CROSS`. A manifest goes next to the output
(`.syx.json`) with each site, the layout and the hashes. The same stock file
always gives the same output.

## What it is

- **The same USB channel and client.** `tools/analogusb.py` works unchanged:
  `cfw` (HELLO answers `analogremote 1.0` and the uptime), `keys`, `turn`,
  `watch` and `peek`. `stats` answers "unknown command".
- **Remote keys are always on.** There is no SETTINGS row to turn them off.
- **Physical keys win, as in analoghealth.** The key mailbox, holds, taps
  and encoder turns are analoghealth's own code (`remote_keys.c`,
  `core_candidate.c/.s`, `remote_keys.s`), unchanged.

## How it differs from analoghealth

| | analoghealth (on its core) | analogremote |
|---|---|---|
| SETTINGS | two added rows | stock |
| top-bar readout, STATS | yes | no |
| timing in the audio interrupt and the task switch | yes | no |
| once-a-second heap walk | yes | no |
| saved projects, kits, sounds | written through the core's storage hooks | stock (no storage hooks) |
| page titles, FX page recall | changed by core 0.4+ | stock |
| sites | 3 + the core's 8 to 56 | 4 |

The four sites are the boot call (to copy the code to the mods' RAM area),
the UI's periodic tick (to pace taps, holds and turns), the SysEx input (for
the channel) and the panel's button driver (to merge remote keys with the
panel's). Only the main OS changes. The output is refused unless every other
section, the bootstrap included, is the stock file's byte for byte.

## What still differs from stock

These come from remote input itself:

- The button driver always runs with interrupts masked.
- Remote encoder counts arrive once per UI tick from the UI task, not from
  the panel, so a remote turn is not the same as a hand turn of the same
  counts.
- SysEx messages to device 0x7D on USB are answered. A stock OS ignores them.
- A few instructions run per UI tick and per incoming SysEx message.
- The main OS is packed by elekloader's packer: the same unpacked image, but
  a different layout in flash.

The key timing also differs from analoghealth. Taps and holds are timed by
the UI tick (50 ms steps), not by DTIM0, because a stock OS need not have
DTIM0 running and this patch leaves it alone. In the emulator, stock 1.74
boots with it stopped, and analoghealth's DTIM0 clock left taps pending
forever there.

So results that do not go through the button driver or the encoder input
can be compared with stock directly. For results that do, check them with
the same input on the stock image in the emulator.

## How it was checked

In the emulator (analog-emu, 25 Oct 2026), each build from a cold boot and
through the OS upgrade path:

- **Rytm, RK74.** The first screen is pixel-identical to stock 1.74's.
  Remote NO taps closed the calibration prompts. GLOBAL opened SETTINGS, and
  ten DOWNs stopped on SYSTEM, the last stock row. On the FILTER page,
  `turn E -60 --step 4` put FREQUENCY in the header and moved FRQ from 127
  to 115, and `turn E 60` brought it back to 127. `turn LEVEL -20 --step 2`
  showed TRK LEVEL 98. PEEK answered.
- **A4, RK56.** GLOBAL opened SETTINGS, and twelve DOWNs stopped on SYSTEM,
  the last stock row. On the page OSC1 opened (NOISE S&H), `turn A -40` and
  `+40` moved the first value (0, then 11). A LEVEL turn showed TRK LEVEL 82.
  PEEK answered.
- **Upgrade path.** A stock 1.74 and a stock 1.56 each took the file as
  elkflash sends it, wrote their flash, restarted from it reporting RK74 or
  RK56, and answered PEEK.

On the units (25 Oct 2026), each flashed with elkflash from stock and then
put back on stock:

- **Rytm MKII, RK74.** Remote NO taps closed the prompts, and SETTINGS
  ended at SYSTEM. On the FILTER page, FRQ went from 127 to 115 and back to
  127. A LEVEL turn of -20 showed TRK LEVEL 98, and +20 then showed 101.
- **Analog Four MKII, RK56.** Remote keys closed the prompts, and SETTINGS
  ended at SYSTEM. A LEVEL turn of -20 showed TRK LEVEL 98, and +20 then
  showed 102.

A LEVEL turn back by the same counts can overshoot, as on the panel, where
the OS's acceleration and dead zone apply.
