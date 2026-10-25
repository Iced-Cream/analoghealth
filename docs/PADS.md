# Pads (Analog Rytm MKII, OS 1.74)

A pad on the Rytm is a pressure sensor as well as a key. The front panel
reports it to the OS with short messages on its serial link: the strike with
its force, the pressure while the pad is held, and the release; the pad's key
bit comes with the panel's other buttons. analoghealth 1.3 can send those same
messages to the OS, so a pad struck from the computer plays its sound at a
velocity and sends aftertouch exactly as a hand on the pad does. It is not a
MIDI note: the OS's MIDI input is not involved, and settings such as PAD DEST
and PRESSURE DST act on it as on the panel.

It can also log what the panel sends, so a real performance can be read and
played back byte for byte.

## Panel messages

A message is two bytes: a header (high nibble the kind, low nibble the pad,
0 BD .. 11 CB) and a value.

| Header | Value | Meaning |
|---|---|---|
| `Ap` | raw force 0..255 | pad p struck |
| `9p` | pressure 0..255 | pad p's pressure while held |
| `Bp` | 0 | pad p released |
| `Cp` | nonzero down, 0 up | pad p's key, as one key message |
| `0p` | nonzero down, 0 up | not a panel message: pad p's key bit through the input bridge, as the panel's button messages carry it |

The OS turns the raw force into the velocity with a threshold the unit keeps
(PAD CAPS reports it; 10 in the emulator): none at the threshold, rising with
force above it, 127 from 63 counts above a threshold of 10. The tools only
pick a force above the threshold for a velocity (`--raw` sends any).
`tools/analogpads.py` `velocity()` gives the mapping and `raw_for()` the force
for a velocity; both were checked against the velocity of the MIDI note the
unit sends for the hit. Pressure arrives as aftertouch, value / 2.

## Commands

The diagnostics channel and envelope of [the key protocol](REMOTE-KEYS.md).
The A4 answers them with status 2, unknown command.

| Command | Arguments | Reply payload |
|---|---|---|
| `0x18 PAD CAPS` | none | `u16 revision (1), u8 pads (12), u8 max events (48), u16 max script ms (10000), u16 log size (512), u8 threshold, u8 remote keys on` |
| `0x19 CAPTURE` | `u8 mode`: 1 clear and start, 0 stop, 2 neither | `u32 bytes logged` |
| `0x1A CAPTURE READ` | `u32 from` | `u32 logged, u32 first, u8 n, n x (u32 time, u8 byte, u8 flags)` |
| `0x1B PAD RUN` | `u32 session, u32 operation, u8 n, n x (u16 ms, u8 header, u8 value)` | a key report |

- **The log** holds the last 512 bytes the panel sent while capturing, with
  the bus-clock time (132 MHz, wrapping about every 32.5 s) at which each
  arrived. Flags: bit 0 the byte was sent by a script, bit 1 the OS took it as
  the start of a message. `first` is the oldest byte still held when `from`
  has been overwritten; a reader that keeps up (every 50 ms or so) loses
  nothing. Logging works whether REMOTE KEYS is on or off; it only reads.
- **A script** is one operation of the key session (its operation number is
  the next one, STATUS and CANCEL work on it as on an ACTION). Up to 48 events,
  times in ms from the start, rising, at most 10 s. The firmware plays it from
  the audio interrupt, 1500 times a second, so each event is sent within
  0.7 ms of its time; a message waits while the panel is in the middle of one
  of its own or the UI's event queue is nearly full, and the script fails if
  it falls 2 s behind. At its end, and on CANCEL, an expiry or REMOTE KEYS
  turned off, the firmware lets go of every pad the script left down: the
  release, the key up and the bridge bit. While a script runs, key actions are
  refused as busy, and a script is refused while the session holds a key.

## Tools

```bash
python tools/analogusb.py pad BD                       # velocity 100, held 100 ms
python tools/analogusb.py pad SD,CP --vel 64 --hold 300 --pressure 40,90,160 --every 50
python tools/analogusb.py pad CH --raw 30 --key none   # the strike alone, no key bit
python tools/analogusb.py padcap 20 --out take.json    # log the panel while someone plays
python tools/analogusb.py padplay take.json [--pads BD,SD]
```

`padcap` prints each message with its time; `padplay` sends the logged pad
messages again with their timing (the strike force, pressure, release and key
messages as they were, and button messages that change a pad's bit as bridge
key events), in scripts split where no pad is down. In analogrun:
`pad PADS [VEL] [HOLD_MS] [raw=R] [pressure=V,..] [every=MS] [key=..]` and
`padplay FILE.json [pads=BD,SD]`. `padplay --log OUT.json` also logs what the
replay fed the OS and compares it with the original, message by message.

## Checked

- Host tests (`tests/test_pads.py`): order and timing of a script, waiting for
  the panel, letting go at the end and on cancel, refusals, the log and its
  wrap, replay from a log.
- In analog-emu (headless, OS 1.74 with core-ar2 0.7): remote hits gave MIDI
  notes on track channel with the velocity `velocity()` predicts (raw 40, 54,
  55, 64, 66, 73 -> 28, 61, 64, 93, 100, 127), pressure gave poly aftertouch
  of value / 2, the log showed the emulator's own pad presses (strike, button
  group bit, release), and `padplay` of that log gave the same notes within
  about 1 ms of the logged timing.
- Not yet on a unit.
