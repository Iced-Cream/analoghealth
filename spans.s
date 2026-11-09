| SPDX-License-Identifier: GPL-2.0-or-later
| analoghealth, DSP spans (a measuring build, not for release): times parts of
| the Analog Rytm MKII 1.74's audio render with the DMA timer that SYSTEM INFO
| uses, so the render's load can be split by part on a unit.
|
| Each wrapped call site's jsr goes to a wrapper here instead. The wrapper takes
| its own return address off the stack (so the callee sees the caller's
| arguments where it expects them), reads DTCN0, calls the stock routine, reads
| DTCN0 again and adds the ticks and one call to its slot. Every register the
| callee gets or returns is passed through untouched (d0 is saved around the
| timer reads). The two 10-byte sites also do the stock instruction that shared
| the site with the call (move.l #0x80,d3 before the reverb; move.w
| 0x8000fbb6,d3 after the delay).
|
| The block `spans` (read it with PEEK; its address is in the build's map):
|   +0   u32  DTCN0 at the last render's end (the time base; it wraps every 32 s)
|   +4   u32  reserved
|   +8   slot 0: the whole render (ev_render_in .. ev_render_out)
|   +16  slot 1: sample layer, call 0x40121134 -> 0x4011c1e0
|   +24  slot 2: sample layer, call 0x40121284 -> 0x4011c6b8
|   +32  slot 3: sample layer, call 0x401212b2 -> 0x40109940
|   +40  slot 4: main mix, call 0x4011fe56 -> 0x4010e6bc (holds slots 6 and 7)
|   +48  slot 5: main mix, call 0x40121406 -> 0x4010e6bc (holds slots 6 and 7)
|   +56  slot 6: delay, call 0x4010e7c4 -> 0x4010da24 (inside the main mix)
|   +64  slot 7: reverb, call 0x4010e81e -> 0x4010d434 (inside the main mix)
| Each slot: u32 ticks (132 MHz, free running, wraps), u32 calls. Read two
| snapshots N seconds apart: a slot's share of the time = d(ticks) / d(+0).

        .include "ar174.inc"

        .equ NSLOTS, 8

        .section .run, "ax"

        .globl  sp_render_in, sp_render_out
sp_render_in:
        move.l  DTCN0, %d0
        move.l  %d0, sp_rin
        rts
sp_render_out:
        move.l  DTCN0, %d0
        move.l  %d0, spans
        sub.l   sp_rin, %d0
        add.l   %d0, spans+8
        addq.l  #1, spans+12
        rts

| SPAN name, stock routine, slot
        .macro  SPAN name, target, slot
        .globl  span_\name
span_\name:
        move.l  %d0, sp_d0
        move.l  (%sp)+, sp_ret_\name
        move.l  DTCN0, %d0
        move.l  %d0, sp_t0_\name
        move.l  sp_d0, %d0
        jsr     \target
        move.l  %d0, sp_d0
        move.l  DTCN0, %d0
        sub.l   sp_t0_\name, %d0
        add.l   %d0, spans+8+(\slot*8)
        addq.l  #1, spans+12+(\slot*8)
        move.l  sp_d0, %d0
        move.l  sp_ret_\name, -(%sp)
        .endm

        SPAN    sl1, 0x4011c1e0, 1
        rts
        SPAN    sl2, 0x4011c6b8, 2
        rts
        SPAN    sl3, 0x40109940, 3
        rts
        SPAN    mixa, 0x4010e6bc, 4
        rts
        SPAN    mixb, 0x4010e6bc, 5
        rts
        SPAN    dly, 0x4010da24, 6
        move.w  0x8000fbb6, %d3         | the stock instruction after the call
        rts

        .globl  span_rev_site
span_rev_site:
        move.l  #0x80, %d3              | the stock instruction before the call
        SPAN    rev, 0x4010d434, 7
        rts

        .section .bss
        .balign 4
        .globl  spans
spans:      .skip 8 + NSLOTS * 8
sp_rin:     .skip 4
sp_d0:      .skip 4
        .irp n, sl1, sl2, sl3, mixa, mixb, dly, rev
sp_ret_\n:  .skip 4
sp_t0_\n:   .skip 4
        .endr
