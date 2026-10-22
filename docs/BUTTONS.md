# Button names

The names `tools/rytmusb.py keys` takes, their logical IDs on the USB channel
and the OS key codes they stand for. The canonical source is `keys.def`;
`tools/keymap.py` reads the same table. The firmware finds where each code
sits on the panel in the unit's own table when it first runs a key command,
and offers only the keys it finds.

- **Names** are the panel's labels, with `BANK_A`-`BANK_H` for the bank keys,
  `PLAYMODE` for the play-mode key (PLAY is the transport key), `BD`-`CB` for
  the pads and `A_PUSH`-`H_PUSH`, `LEVEL_PUSH` for the encoder pushes.
- **Pads:** a pad from the computer is the pad's key event only. It does not
  play the sound, which comes with the pad's velocity; the helper sends no
  velocity. In the emulator a remote `BD` sent no MIDI note, where a pad hit
  sends one.
- **Not in the map:** knob turns, the pads' velocity and pressure, master
  volume and power.

| Name | Logical/USB ID | OS key code |
|---|---:|---:|
| `1` | 1 | 0x00 |
| `2` | 2 | 0x01 |
| `3` | 3 | 0x02 |
| `4` | 4 | 0x03 |
| `5` | 5 | 0x04 |
| `6` | 6 | 0x05 |
| `7` | 7 | 0x06 |
| `8` | 8 | 0x07 |
| `9` | 9 | 0x08 |
| `10` | 10 | 0x09 |
| `11` | 11 | 0x0a |
| `12` | 12 | 0x0b |
| `13` | 13 | 0x0c |
| `14` | 14 | 0x0d |
| `15` | 15 | 0x0e |
| `16` | 16 | 0x0f |
| `FUNC` | 17 | 0x80 |
| `TRK` | 18 | 0x86 |
| `RTRG` | 19 | 0x85 |
| `MUTE` | 20 | 0x11 |
| `PLAYMODE` | 21 | 0xb5 |
| `QPER` | 22 | 0xb1 |
| `SAMPLER` | 23 | 0xb4 |
| `GLOBAL` | 24 | 0xb0 |
| `CHRO` | 25 | 0x12 |
| `SCNE` | 26 | 0x13 |
| `PERF` | 27 | 0x14 |
| `FIX` | 28 | 0xb3 |
| `TEMPO` | 29 | 0x83 |
| `YES` | 30 | 0x44 |
| `NO` | 31 | 0x45 |
| `UP` | 32 | 0x42 |
| `DOWN` | 33 | 0x43 |
| `LEFT` | 34 | 0x40 |
| `RIGHT` | 35 | 0x41 |
| `TRIG` | 36 | 0x30 |
| `SRC` | 37 | 0x31 |
| `SMPL` | 38 | 0x32 |
| `FLTR` | 39 | 0x33 |
| `AMP` | 40 | 0x34 |
| `LFO` | 41 | 0x35 |
| `FX` | 42 | 0x10 |
| `FILL` | 43 | 0xb2 |
| `PAGE` | 44 | 0x84 |
| `CHAIN` | 45 | 0x82 |
| `SONG` | 46 | 0x81 |
| `RECORD` | 47 | 0x50 |
| `PLAY` | 48 | 0x51 |
| `STOP` | 49 | 0x52 |
| `BANK_A` | 50 | 0xc0 |
| `BANK_B` | 51 | 0xc1 |
| `BANK_C` | 52 | 0xc2 |
| `BANK_D` | 53 | 0xc3 |
| `BANK_E` | 54 | 0xc4 |
| `BANK_F` | 55 | 0xc5 |
| `BANK_G` | 56 | 0xc6 |
| `BANK_H` | 57 | 0xc7 |
| `BD` | 58 | 0x90 |
| `SD` | 59 | 0x91 |
| `RS` | 60 | 0x92 |
| `CP` | 61 | 0x93 |
| `BT` | 62 | 0x94 |
| `LT` | 63 | 0x95 |
| `MT` | 64 | 0x96 |
| `HT` | 65 | 0x97 |
| `CH` | 66 | 0x98 |
| `OH` | 67 | 0x99 |
| `CY` | 68 | 0x9a |
| `CB` | 69 | 0x9b |
| `A_PUSH` | 70 | 0x70 |
| `B_PUSH` | 71 | 0x71 |
| `C_PUSH` | 72 | 0x72 |
| `D_PUSH` | 73 | 0x73 |
| `E_PUSH` | 74 | 0x74 |
| `F_PUSH` | 75 | 0x75 |
| `G_PUSH` | 76 | 0x76 |
| `H_PUSH` | 77 | 0x77 |
| `LEVEL_PUSH` | 78 | 0x78 |
