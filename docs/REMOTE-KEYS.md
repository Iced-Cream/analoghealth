# Bounded key protocol, revision 5

Analog Rytm MKII, OS 1.74, and Analog Four MKII, OS 1.56. Revision 3 is digihealth's revision 2 with 128-bit
key maps, because the Rytm has 78 keys, the A4 89, and revision 2's maps hold 64.
Revision 4 adds action 4, TURN, for the encoders, and the encoder count to
CAPABILITIES. The
commands, actions, states and rules are revision 2's. The messages use the
USB-only SysEx channel the diagnostics use; HELLO, STATS, PEEK and
diagnostics revision 1 are unchanged. Revision 5 lets several keys be held at once (below); its messages are
revision 4's.

DIN source masks 8 and 16 are refused; USB source masks 2 and 4 are accepted.
This is ownership and duplicate suppression, not authentication. The SETTINGS
row REMOTE KEYS (on at power-on) is the unit's own switch: off, every key
command, CAPABILITIES included, is answered with status 6 and no payload, and
the next UI tick cancels the session, letting go of what it holds as CANCEL
does. Diagnostics answer either way.

```
request F0 00 20 3C 7D 00 <pack7: u16 sequence, u8 command, arguments> F7
reply   F0 00 20 3C 7D 00 <pack7: u16 sequence, u8 command|0x80, u8 status, payload> F7
```

Integers are big-endian. Packing places the high bits of up to seven bytes in
header bits 6 through 0, followed by their low seven bits. Every encoded byte
must be below 128; unused header bits must be zero. Key commands require an
exact decoded length, refusing truncated, oversized and noncanonical input.

| Command | Arguments | Successful reply payload |
|---|---|---|
| `0x10 CAPABILITIES` | none | `u16 revision, u8 product, u8 OS_major, u8 OS_minor, u128 keys, u16 default_tap_ms, u16 max_tap_ms, u16 max_hold_ms, u8 encoders` (28 bytes) |
| `0x11 OPEN` | `u32 session` | report |
| `0x12 ACTION` | `u32 session, u32 operation, u8 action, u8 key, u16 duration_ms` | report |
| `0x13 STATUS` | `u32 session, u32 operation` | report |
| `0x14 CANCEL` | `u32 session` | report |

- **Capabilities:** revision 5 (4 on older builds); product 3 (Analog Rytm MKII), OS 1.74, or product 4 (Analog Four MKII), OS 1.56. The
  128-bit map marks logical key ID minus one; 78 bits are set on the Rytm, 89 on the A4. Default tap
  100 ms, maximum tap 250 ms, maximum press lease 2000 ms, 9 encoders on the Rytm and 11 on the A4. The client checks
  revision, product, OS and map before OPEN, and refuses names the map lacks
  before ACTION.
- **Report:** `u32 session, u32 operation, u8 state, u8 reason, u128
  owned_keys, u8 cleanup_pending` (27 bytes). States: 0 none, 1 accepted,
  2 completed, 3 cancelled, 4 expired, 5 failed. Reasons and envelope
  statuses: 0 OK, 1 invalid or refused, 2 unknown command, 3 busy, 4 stale
  session or operation, 5 physical overlap, 6 remote keys off. Failed
  envelopes carry no payload.
- **Actions:** 1 PRESS (a 1..2000 ms lease), 2 RELEASE (duration zero),
  3 TAP (1..250 ms), 4 TURN. For TURN, `key` is the encoder: on the Rytm 1-8 =
  A-H and 9 = LEVEL, on the A4 1-10 = A-J and 11 = LEVEL; and `duration` is `s8 step << 8 | u8 ticks`: the firmware sends
  `step` counts (1..30, either sign) on each of `ticks` UI ticks (1..30), one
  turn message a tick as the panel's encoders send them, and the action
  completes after the last. TURN may run while a key is held (FUNC + turn).
  A longer turn is several TURN actions. Key IDs are those of `keys-ar2.def` or `keys-a4.def` (see [the button
  names](BUTTONS.md)). The stock driver makes the UI events; no gesture or
  menu policy is encoded.

Sessions must be nonzero. One operation can be pending. Revision 5 allows up
to four persistent holds at once (revision 4: one), each with its own lease,
and taps and turns while they are held, across groups too: trig + FUNC +
RECORD is PRESS trig, PRESS FUNC, TAP RECORD, then the releases. A PRESS of a
fifth key is refused as busy. The first lease to run out ends the session's
holds, as an expiry. A pad script (PAD RUN) may play while keys are held, but
not while the session holds a pad's own key. Operations start
at 1 and go up by one without wrapping. Duplicate IDs with identical payloads
return the latest state; conflicting duplicates and older or skipped IDs are
refused. A repeated PRESS never renews a hold's lease. OPEN of the current
session never resets its deadline or history. A different session may open
only when no operation or cleanup is pending and no synthetic key is owned.

STATUS operation zero queries the latest report, before the first action too.
Other operation IDs must match the latest accepted operation. CANCEL is
idempotent, closes the session after cleanup and stays available during
pending work. A closed session permits status, duplicate-result queries and
cancel, but no new actions or reopen.

The MIDI receiver validates and queues work under a short interrupt mask; only
the UI tick (20 Hz) calls the input bridge. Accepted means queued. Completed
means the driver took the transition (the release of a tap too), not that a
view handled it. A lost ACTION reply is resolved with STATUS for the same
session and operation, never a new action. There is no exactly-once guarantee
across a reset.

## The input bridge

The bridge (`core_candidate.c`, digihealth's) sits where the panel's button
states enter the OS. It merges the computer's keys with the panel's and hands
the result on, so the stock OS makes every key event, repeat and release as
for a real press. Newly taking a key held on the panel is refused; a panel
press of a key the computer holds survives the computer's release.

Each key's place on the panel comes from the unit's own table, looked up on
the first key command; a key the table lacks is not offered. A transition is
sent only while the UI's event queue has room (16 events or fewer waiting and
16 free places); otherwise the UI tick retries later and the action stays
accepted.

DTIM0 runs at the 132 MHz bus clock (measured: 1500 audio blocks a second
come 88,000 counter ticks apart), with unsigned subtraction across the
counter's wrap. Deadlines are checked only when the UI task runs, so a stalled
UI delays cleanup, and a stall longer than the counter's wrap (about
32.5 seconds) can delay it further. The client cancels in its context manager;
disconnects rely on the firmware's leases.

The bridge is private to this mod, as in digihealth; core-ar2's exports are
unchanged.
