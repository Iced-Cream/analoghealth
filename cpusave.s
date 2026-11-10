| SPDX-License-Identifier: GPL-2.0-or-later
| TEST BRANCH ONLY (sample-layer skip unit run): this row switches core_sample_skip_want, not the voice skip.
| analoghealth, CPU SAVE (Rytm only, core-ar2 0.14): the SETTINGS row that
| turns core's silent-voice skip on or off, as digihealth's FAST AUDIO row
| turns its render copy on. Core skips a voice slot's machine call while the
| slot is silent (core-ar2 README, "The silent-voice skip (0.14)"); that lowers
| the DSP load, and the slot's next note starts from its power-on state, which
| changes the sound slightly. So it is off unless the user turns it on.
|
| The choice is kept in the project (a 1-bit project-scope storage field,
| ah_cpu_save), so it survives a power cycle and travels with the project:
| ev_project copies it into core's switch after every project load (at boot
| too) and after CREATE NEW. Core's own switch, core_voice_skip_want, is the
| user's choice only; mods that need stock's exact render hold the skip off
| through core_voice_skip_hold_add/_release (a count), which this row neither reads nor writes.
        .include "ar174.inc"

        .section .run, "ax"

| ev_settings handler: the CPU SAVE row.
        .globl  cs_settings
cs_settings:
        pea     row_cpu
        move.l  8(%sp), -(%sp)          | the menu
        jsr     core_additem
        addq.l  #8, %sp
        rts

| label() -> std::string, returned through a0
cs_label:
        link    %a6, #-4
        move.l  %d2, -(%sp)
        pea     -1(%a6)
        pea     str_cpu
        move.l  %a0, %d2
        move.l  %a0, -(%sp)
        jsr     STR_CTOR
        lea     12(%sp), %sp
        move.l  %d2, %d0
        move.l  -8(%a6), %d2
        unlk    %a6
        rts

| select(payload, item): toggle
cs_select:
        moveq   #0, %d0
        tst.b   cs_on
        seq     %d0
        andi.l  #1, %d0
        bra.s   cs_set

| change(payload, item, delta): RIGHT on, LEFT off
cs_change:
        move.l  12(%sp), %d0
        beq.s   9f
        sgt     %d0
        andi.l  #1, %d0
        bra.s   cs_set
9:      rts

| cs_set: d0 = 0 or 1, the caller's frame (payload at 4(sp)) untouched.
| Keeps the value in the project and core's switch, marks the project
| edited (a load asks first, as after a stock edit), then redraws the menu.
cs_set:
        cmp.b   cs_on, %d0
        beq.s   1f                      | no change: nothing to keep
        move.b  %d0, cs_on
        move.b  %d0, core_sample_skip_want
        move.l  %d0, -(%sp)             | value
        clr.l   -(%sp)                  | index: unused
        clr.l   -(%sp)                  | item: unused (project scope)
        pea     ah_cpu_save             | the field's descriptor
        jsr     core_store_set
        lea     16(%sp), %sp
        jsr     core_project_edited
1:      movea.l 4(%sp), %a0
        movea.l (%a0), %a0              | the menu
        lea     0x38(%a0), %a0
        move.l  %a0, 4(%sp)
        jmp     INVALIDATE

| draw(payload, item, bmp, x, y): the checkbox
cs_draw:
        moveq   #0, %d0
        tst.b   cs_on
        beq.s   1f
        moveq   #0x1c, %d0
1:      add.l   CHECKBOXES, %d0
        move.l  12(%sp), %d1
        move.l  %d1, 4(%sp)
        move.l  %d0, 8(%sp)
        move.l  16(%sp), %d1
        move.l  %d1, 12(%sp)
        move.l  20(%sp), %d1
        move.l  %d1, 16(%sp)
        clr.l   20(%sp)
        jmp     BLIT

| ev_project handler, f(how, dropped): after every project load (at boot
| too) and CREATE NEW, the project's choice goes into core's switch. A
| project saved without this field, or by another layout, reads 0: off.
        .globl  cs_project
cs_project:
        clr.l   -(%sp)                  | index: unused
        clr.l   -(%sp)                  | item: unused
        pea     ah_cpu_save
        jsr     core_store_get
        lea     12(%sp), %sp
        tst.l   %d0
        sne     %d0
        andi.l  #1, %d0
        move.b  %d0, cs_on
        move.b  %d0, core_sample_skip_want
        rts

        .balign 4
row_cpu:    .long   cs_label, cs_select, cs_draw, cs_change
str_cpu:    .asciz  "CPU SAVE"
        .balign 4

        .section .bss
cs_on:      .skip 4                 | the row's state (byte 0), set by cs_project at boot
