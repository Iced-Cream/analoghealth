# Button names

The names `tools/analogusb.py keys` takes, their logical IDs on the USB channel
and the OS key codes they stand for, for each device. The canonical sources
are `keys-ar2.def` (Rytm) and `keys-a4.def` (A4); `tools/keymap.py` reads the
same files. The firmware finds where each code sits on the panel in the unit's
own table when it first runs a key command, and offers only the keys it finds.

- **Names** are the panel's labels as the OS names them, with `BANK_A`-`BANK_H`
  for the bank keys and `A_PUSH`... `LEVEL_PUSH` for the encoder pushes.
  On the Rytm `PLAYMODE` is the play-mode key (PLAY is the transport key) and
  `BD`-`CB` are the pads. On the A4 `SONG_MENU` is the SONG key of the
  KIT/SND/... row (SONG is the song-mode key), `OCT_DOWN`/`OCT_UP` the octave
  keys, `T1`-`T4` the track keys and `KB_C`... `KB_C2` the keyboard, whose
  codes rise by a semitone.
- **Rytm pads:** a pad from the computer is the pad's key event only. It does
  not play the sound, which comes with the pad's velocity; the helper sends no
  velocity. In the emulator a remote `BD` sent no MIDI note, where a pad hit
  sends one.
- **Encoders** are turned with `analogusb.py turn` (A-H and LEVEL on the Rytm,
  A-J and LEVEL on the A4), not named here; their pushes are the `_PUSH` keys.
- **Not in the maps:** the Rytm pads' velocity and pressure, master volume and
  power.

## Analog Rytm MKII (OS 1.74)

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

## Analog Four MKII (OS 1.56)

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
| `GLOBAL` | 18 | 0xb0 |
| `QPER` | 19 | 0xb1 |
| `TEMPO` | 20 | 0x83 |
| `KIT` | 21 | 0x90 |
| `SND` | 22 | 0x91 |
| `MIX` | 23 | 0xb3 |
| `PERF` | 24 | 0x30 |
| `TRK` | 25 | 0x92 |
| `PTN` | 26 | 0x93 |
| `SONG_MENU` | 27 | 0x94 |
| `YES` | 28 | 0x44 |
| `NO` | 29 | 0x45 |
| `UP` | 30 | 0x42 |
| `DOWN` | 31 | 0x43 |
| `LEFT` | 32 | 0x40 |
| `RIGHT` | 33 | 0x41 |
| `ARP` | 34 | 0x31 |
| `NOTE` | 35 | 0x32 |
| `OSC1` | 36 | 0x33 |
| `OSC2` | 37 | 0x34 |
| `FLTR` | 38 | 0x35 |
| `AMP` | 39 | 0x36 |
| `ENV` | 40 | 0x37 |
| `LFO` | 41 | 0x38 |
| `FX` | 42 | 0x24 |
| `CV` | 43 | 0x25 |
| `FILL` | 44 | 0xb2 |
| `PAGE` | 45 | 0x84 |
| `CHAIN` | 46 | 0x82 |
| `SONG` | 47 | 0x81 |
| `RECORD` | 48 | 0x50 |
| `PLAY` | 49 | 0x51 |
| `STOP` | 50 | 0x52 |
| `OCT_DOWN` | 51 | 0xa5 |
| `OCT_UP` | 52 | 0xa4 |
| `TRNS` | 53 | 0x85 |
| `T1` | 54 | 0x20 |
| `T2` | 55 | 0x21 |
| `T3` | 56 | 0x22 |
| `T4` | 57 | 0x23 |
| `BANK_A` | 58 | 0xc0 |
| `BANK_B` | 59 | 0xc1 |
| `BANK_C` | 60 | 0xc2 |
| `BANK_D` | 61 | 0xc3 |
| `BANK_E` | 62 | 0xc4 |
| `BANK_F` | 63 | 0xc5 |
| `BANK_G` | 64 | 0xc6 |
| `BANK_H` | 65 | 0xc7 |
| `KB_C` | 66 | 0x10 |
| `KB_CS` | 67 | 0x11 |
| `KB_D` | 68 | 0x12 |
| `KB_DS` | 69 | 0x13 |
| `KB_E` | 70 | 0x14 |
| `KB_F` | 71 | 0x15 |
| `KB_FS` | 72 | 0x16 |
| `KB_G` | 73 | 0x17 |
| `KB_GS` | 74 | 0x18 |
| `KB_A` | 75 | 0x19 |
| `KB_AS` | 76 | 0x1a |
| `KB_B` | 77 | 0x1b |
| `KB_C2` | 78 | 0x1c |
| `A_PUSH` | 79 | 0x70 |
| `B_PUSH` | 80 | 0x71 |
| `C_PUSH` | 81 | 0x72 |
| `D_PUSH` | 82 | 0x73 |
| `E_PUSH` | 83 | 0x74 |
| `F_PUSH` | 84 | 0x75 |
| `G_PUSH` | 85 | 0x76 |
| `H_PUSH` | 86 | 0x77 |
| `I_PUSH` | 87 | 0x78 |
| `J_PUSH` | 88 | 0x79 |
| `LEVEL_PUSH` | 89 | 0x7a |
