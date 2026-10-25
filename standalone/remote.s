| SPDX-License-Identifier: GPL-2.0-or-later
| analogremote: analoghealth's REMOTE KEYS and its read-only USB channel
| (HELLO, PEEK), and nothing else, patched straight into a stock OS by
| standalone/patch.py, with no elekloader core. No SETTINGS rows, no
| SYSTEM INFO readout, no render or task-switch timing, no heap walk:
| remote keys are always on. The key and encoder code is analoghealth's own
| (remote_keys.c, core_candidate.c/.s, remote_keys.s), unchanged.
|
| Four sites (patch.py): an early call of the OS start-up (boot, below), the UI's
| periodic compose check (tick_hook), the SysEx input (hook_sysex) and the
| panel's button driver (core_panel_buttons, core_candidate.c).
|
| patch.py gives the addresses as --defsym:
|   VBR_FN       what the boot site called (from your stock file)
|   TICK_FN      what the UI tick site called (from your stock file)
        .ifdef  A4156
        .include "a4156.inc"
        .else
        .include "ar174.inc"
        .endif

        .equ TICKS_PER_S, 20            | the UI's compose check runs at 20 Hz
        .equ CLOCK_PER_TICK, 6600000    | 50 ms in 132 MHz counts
        .equ DIAG_PROTO,  1
        .equ C_HELLO,     1             | -> u16 channel version, u32 seconds, name
        .equ C_PEEK,      3             | u32 addr, u16 len -> u32 addr, bytes
        .equ PEEK_MAX,    1024
        .equ PEEK_NRANGES, 2
        .equ RQ_MAX,      16

| ============================ .boot =======================================
| Appended to the main OS, so the bootstrap unpacks it with the OS. It runs
| once, from an early call of the OS start-up, before the OS clears its RAM: it copies
| the run image to the mods' DDR area, zeroes .bss and goes on to the call
| it replaced (whose rts returns past the site).
        .section .boot, "ax"
        .globl  boot
boot:
        lea     __run_load, %a0
        lea     __run_start, %a1
        move.l  #__run_words, %d0
        beq.s   2f
1:      move.l  (%a0)+, (%a1)+
        subq.l  #1, %d0
        bne.s   1b
2:      lea     __bss_start, %a1        | DDR holds noise at power-on
        move.l  #__bss_words, %d0
        beq.s   4f
3:      clr.l   (%a1)+
        subq.l  #1, %d0
        bne.s   3b
4:      jmp     VBR_FN

        .section .run, "ax"

| ---- the UI tick ----------------------------------------------------------
| Replaces the compose check's call of TICK_FN, (sp) = return, 4(sp) = the
| view controller. UI task. Runs the remote keys' tick, then the stock
| routine with the same stack, so the caller gets stock's d0.
        .globl  tick_hook
tick_hook:
        lea     -8(%sp), %sp
        movem.l %d1/%a1, (%sp)          | C may use them; stock's caller keeps them
        move.l  #CLOCK_PER_TICK, %d0
        add.l   %d0, clock
        addq.l  #1, ticks
        moveq   #TICKS_PER_S, %d0
        cmp.l   ticks, %d0
        bgt.s   1f
        clr.l   ticks
        addq.l  #1, secs
1:      move.l  12(%sp), -(%sp)         | ctrl
        jsr     rk_tick
        addq.l  #4, %sp
        movem.l (%sp), %d1/%a1
        lea     8(%sp), %sp
        jmp     TICK_FN

| rk_now() -> the remote keys' clock (remote_keys.c), in 132 MHz counts as
| analoghealth's (DTIM0), but advanced by the UI tick: a stock OS need not
| have DTIM0 running, and this patch leaves it as it finds it. Taps and holds
| are timed to the tick (50 ms); the clock wraps after about 32 s, as DTIM0
| does, and the code only compares differences.
        .globl  rk_now
rk_now:
        move.l  clock, %d0
        rts

| ---- the USB channel ------------------------------------------------------
| F0 00 20 3C 7D 00 <pack7: u16 seq, u8 cmd, args> F7, answered with
| F0 00 20 3C 7D 00 <pack7: u16 seq, u8 cmd|0x80, u8 status, payload> F7
| over USB: analoghealth's protocol, so tools/analogusb.py is the other end.
| STATS is not here (it answers "unknown command").
| Called from the SysEx input for every incoming SysEx message, in the MIDI
| input task. It repeats the work its call displaced. A stock OS ignores
| device 0x7D.
        .globl  hook_sysex
        .ifdef  A4156
| The A4: a0 = the message, F0 to F7; d2 = its length; d3 = where it came
| from (2 or 4 USB). a0, d1, d2, d3 must survive; a2 is reloaded.
hook_sysex:
        lea     -20(%sp), %sp
        movem.l %d1-%d3/%a0-%a1, (%sp)
        move.l  %d2, %d1
        move.l  %d3, %d2
        bsr.w   sx_check
        movem.l (%sp), %d1-%d3/%a0-%a1
        lea     20(%sp), %sp
        lea     SYSEX_A2, %a2           | the work the call displaced
        rts
        .else
| The Rytm: a0 = a1 = the message, F0 to F7; d1 = its length; d2 = where it
| came from (2 or 4 USB). a0, a1, d1, d2 must survive; d0, d3, a2 are free.
hook_sysex:
        lea     -16(%sp), %sp
        movem.l %d1-%d2/%a0-%a1, (%sp)
        bsr.w   sx_check
        movem.l (%sp), %d1-%d2/%a0-%a1
        lea     16(%sp), %sp
        lea     SYSEX_A3, %a3           | the work the call displaced
        rts
        .endif

| sx_check(a0 the message, d1 its length, d2 its source): answers ours.
sx_check:
        moveq   #7, %d0
        cmp.l   %d0, %d1
        blt.s   9f                      | shorter than header + F7
        moveq   #2, %d0                 | USB only
        cmp.l   %d0, %d2
        beq.s   1f
        moveq   #4, %d0
        cmp.l   %d0, %d2
        bne.s   9f
1:      lea     diag_hdr, %a2           | F0 00 20 3C 7D 00
        moveq   #6, %d3
2:      move.b  (%a0)+, %d0
        cmp.b   (%a2)+, %d0
        bne.s   9f
        subq.l  #1, %d3
        bne.s   2b
        move.l  %d1, %d0                | a0 = the body; d0 = its length
        subq.l  #7, %d0
        moveq   #-9, %d3                | the last byte is F7?
        cmp.b   0(%a0,%d0.l), %d3
        bne.s   9f
        move.l  %d0, -(%sp)
        move.l  %a0, -(%sp)
        bsr.w   diag_rx
        addq.l  #8, %sp
9:      rts

| diag_rx(const u8 *body, u32 len): body is what lies between the 6-byte
| header and the F7. Answers on USB, through the OS's SysEx sender.
diag_rx:
        lea     -20(%sp), %sp
        movem.l %d2-%d5/%a2, (%sp)
        movea.l 24(%sp), %a0
        move.l  28(%sp), %d0
        pea     rp
        move.l  %d0, -(%sp)
        move.l  %a0, -(%sp)
        jsr     rk_receive              | a key command: its reply is in rp
        lea     12(%sp), %sp
        tst.l   %d0
        beq.s   90f
        lea     rp, %a2
        lea     0(%a2,%d0.l), %a1
        bra.w   8f
90:     movea.l 24(%sp), %a0
        move.l  28(%sp), %d0
        lea     rq+1, %a1               | seq at rq+1 puts the PEEK args aligned
        moveq   #RQ_MAX, %d1
        bsr.w   unpack7
        move.l  %d0, %d5                | request bytes
        moveq   #3, %d1
        cmp.l   %d1, %d5
        blt.w   9f                      | too short to answer
        lea     rp, %a2
        move.b  rq+1, %d0               | seq
        move.b  %d0, (%a2)
        move.b  rq+2, %d0
        move.b  %d0, 1(%a2)
        moveq   #0, %d2
        move.b  rq+3, %d2               | cmd
        move.l  %d2, %d0
        bset    #7, %d0
        move.b  %d0, 2(%a2)
        clr.b   3(%a2)                  | status: ok
        lea     4(%a2), %a1             | the payload
        moveq   #C_HELLO, %d1
        cmp.l   %d1, %d2
        beq.s   1f
        moveq   #C_PEEK, %d1
        cmp.l   %d1, %d2
        beq.s   5f
        moveq   #2, %d0                 | unknown command
        move.b  %d0, 3(%a2)
        bra.w   8f
1:      moveq   #DIAG_PROTO, %d0        | HELLO
        move.w  %d0, (%a1)+
        move.l  secs, (%a1)+
        lea     str_name, %a0
2:      move.b  (%a0)+, %d0
        move.b  %d0, (%a1)+
        bne.s   2b
        bra.w   8f
5:      moveq   #9, %d1                 | PEEK: seq, cmd, addr, len
        cmp.l   %d1, %d5
        blt.s   7f
        move.l  rq+4, %d3               | address
        moveq   #0, %d4
        move.w  rq+8, %d4               | length
        beq.s   7f
        cmpi.l  #PEEK_MAX, %d4
        bhi.s   7f
        lea     peek_ranges, %a0        | inside one range, end included
        moveq   #PEEK_NRANGES, %d2
55:     move.l  %d3, %d1
        sub.l   (%a0), %d1              | the offset into this range
        bcs.s   56f                     | below it
        add.l   %d4, %d1                | where the read ends
        move.l  4(%a0), %d0
        sub.l   (%a0), %d0              | the range's size
        cmp.l   %d0, %d1
        bls.s   57f
56:     addq.l  #8, %a0
        subq.l  #1, %d2
        bne.s   55b
        bra.s   7f
57:     move.l  %d3, (%a1)+
        movea.l %d3, %a0
6:      move.b  (%a0)+, %d0
        move.b  %d0, (%a1)+
        subq.l  #1, %d4
        bne.s   6b
        bra.s   8f
7:      moveq   #1, %d0                 | refused
        move.b  %d0, 3(%a2)
8:      move.l  %a1, %d0                | send: the reply ends at a1
        sub.l   %a2, %d0
        movea.l %a2, %a0
        lea     tx+6, %a1
        bsr.w   pack7
        lea     tx, %a0
        move.l  diag_hdr, %d1           | F0 00 20 3C
        move.l  %d1, (%a0)
        move.w  diag_hdr+4, %d1         | 7D 00
        move.w  %d1, 4(%a0)
        addq.l  #6, %d0
        moveq   #-9, %d1                | F7
        move.b  %d1, 0(%a0,%d0.l)
        addq.l  #1, %d0
        pea     2.w                     | port: USB
        clr.l   -(%sp)                  | no abort flag
        move.l  %d0, -(%sp)
        pea     tx
        jsr     SYSEX_SEND
        lea     16(%sp), %sp
9:      movem.l (%sp), %d2-%d5/%a2
        lea     20(%sp), %sp
        rts

| unpack7(a0 src, d0 len, a1 dst, d1 room) -> d0 bytes out. MidiRpc's packing:
| each group of up to 7 bytes follows a byte whose bit 6-k is byte k's bit 7.
unpack7:
        lea     -16(%sp), %sp
        movem.l %d2-%d5, (%sp)
        move.l  %d0, %d2                | bytes left
        moveq   #0, %d3                 | bytes out
1:      tst.l   %d2
        beq.s   9f
        moveq   #0, %d4
        move.b  (%a0)+, %d4             | the group's top bits
        subq.l  #1, %d2
        moveq   #7, %d0
2:      tst.l   %d2
        beq.s   9f
        add.l   %d4, %d4                | this byte's top bit to bit 7
        move.b  (%a0)+, %d5
        subq.l  #1, %d2
        bclr    #7, %d5
        btst    #7, %d4
        beq.s   3f
        bset    #7, %d5
3:      cmp.l   %d1, %d3
        bcc.s   4f                      | no room: drop it
        move.b  %d5, (%a1)+
        addq.l  #1, %d3
4:      subq.l  #1, %d0
        bne.s   2b
        bra.s   1b
9:      move.l  %d3, %d0
        movem.l (%sp), %d2-%d5
        lea     16(%sp), %sp
        rts

| pack7(a0 src, d0 len, a1 dst) -> d0 bytes out.
pack7:
        lea     -16(%sp), %sp
        movem.l %d2-%d4/%a2, (%sp)
        move.l  %a1, %d4                | where the output starts
1:      tst.l   %d0
        beq.s   9f
        movea.l %a1, %a2                | the group's header byte
        addq.l  #1, %a1
        moveq   #0, %d2                 | header
        moveq   #0x40, %d3              | its bit for this byte
2:      move.b  (%a0)+, %d1
        btst    #7, %d1
        beq.s   3f
        or.l    %d3, %d2
3:      bclr    #7, %d1
        move.b  %d1, (%a1)+
        subq.l  #1, %d0
        beq.s   4f
        lsr.l   #1, %d3
        bne.s   2b
4:      move.b  %d2, (%a2)
        bra.s   1b
9:      move.l  %a1, %d0
        sub.l   %d4, %d0
        movem.l (%sp), %d2-%d4/%a2
        lea     16(%sp), %sp
        rts

        .balign 4
peek_ranges: .long  0x40000000, 0x48000000     | DDR, cached
             .long  0x80000000, 0x80010000     | the 64 KB SRAM
diag_hdr:   .byte   0xF0, 0x00, 0x20, 0x3C, 0x7D, 0x00
str_name:   .asciz  "analogremote 1.0"
        .balign 4

        .section .bss
        .balign 4
        .globl  rk_off
rk_off:     .skip 4             | REMOTE KEYS off: always 0 here (no row)
clock:      .skip 4
ticks:      .skip 4
secs:       .skip 4
rq:         .skip RQ_MAX + 4    | a request, unpacked
rp:         .skip 8 + PEEK_MAX + 8  | a reply, before packing
tx:         .skip 1200          | a reply as sent: 6 + 1188 + 1
