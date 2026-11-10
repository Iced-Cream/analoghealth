| SPDX-License-Identifier: GPL-2.0-or-later
| analoghealth, DSP SETTINGS (Rytm only, core-ar2 0.14): a GLOBAL menu row
| that opens a menu of core's load savers, one checkbox each, all off until
| ticked. Core keeps one switch per saver (a want byte) and lets a mod that
| needs stock's exact render hold any of them off; this menu writes only the
| want bytes, the user's choice, and never the holds.
|
|   VOICE SKIP    core_voice_skip_want   silent voices' machine calls (changes the sound slightly)
|   SAMPLE SKIP   core_sample_skip_want  idle sample layers (exact)
|   FX SKIP       core_fx_skip_want      zero sends and a silent delay (exact)
|   REVERB CLEAR  core_reverb_clear_want a silent reverb, cleared and skipped (changes the sound;
|                                        acts only while FX SKIP is ticked)
|   UI SKIP       core_ui_skip_want      the screen's repeated view look-ups (exact)
|
| Each choice is kept in the project (a 1-bit project-scope storage field),
| so it survives a power cycle and travels with the project: ds_project
| copies them into core's switches after every project load (at boot too)
| and after CREATE NEW. VOICE SKIP keeps CPU SAVE's field (ah_cpu_save), so
| a project saved with CPU SAVE ticked loads with VOICE SKIP ticked.
        .include "ar174.inc"

        .section .run, "ax"

NROWS = 5

| ---- the GLOBAL row ---------------------------------------------------------

| ev_settings handler
        .globl  ds_settings
ds_settings:
        pea     row_dsp
        move.l  8(%sp), -(%sp)          | the menu
        jsr     core_additem
        addq.l  #8, %sp
        rts

| select(payload, item): open the submenu
ds_open:
        movea.l 4(%sp), %a0
        pea     ds_menu
        move.l  (%a0), -(%sp)           | the GLOBAL menu
        jsr     core_submenu_open
        addq.l  #8, %sp
        rts

ds_label_dsp:
        lea     str_dsp, %a1
        bra     ds_string

| draw(payload, item, bmp, x, y): the row's icon, as stock's GLOBAL rows
| draw theirs (blit, not centred)
ds_icon:
        move.l  12(%sp), 4(%sp)         | bmp
        lea     icon_load, %a0
        move.l  %a0, 8(%sp)
        move.l  16(%sp), 12(%sp)        | x
        move.l  20(%sp), 16(%sp)        | y
        clr.l   20(%sp)
        jmp     BLIT

| change: nothing, as stock's rows that open a submenu
ds_nothing:
        rts

| ---- the submenu's rows -----------------------------------------------------
| Each row's four callbacks put the row's number in d1 and go on to one
| shared routine.

        .macro  ROW n
ds_label_\n:
        moveq   #\n, %d1
        bra     ds_label
ds_select_\n:
        moveq   #\n, %d1
        bra     ds_select
ds_draw_\n:
        moveq   #\n, %d1
        bra     ds_draw
ds_change_\n:
        moveq   #\n, %d1
        bra     ds_change
        .endm

        ROW     0
        ROW     1
        ROW     2
        ROW     3
        ROW     4

| a1 = row d1's entry in ds_rows (want, field, label); d0 changed
ds_entry:
        lea     ds_rows, %a1
        move.l  %d1, %d0                | x 12
        lsl.l   #2, %d0
        adda.l  %d0, %a1
        adda.l  %d0, %a1
        adda.l  %d0, %a1
        rts

| label() -> std::string through a0
ds_label:
        bsr     ds_entry
        movea.l 8(%a1), %a1
| ds_string: a std::string of the C string a1 at a0
ds_string:
        link    %a6, #-4
        move.l  %d2, -(%sp)
        move.l  %a0, %d2
        pea     -1(%a6)
        move.l  %a1, -(%sp)
        move.l  %a0, -(%sp)
        jsr     STR_CTOR
        lea     12(%sp), %sp
        move.l  %d2, %d0
        movea.l %d2, %a0
        move.l  -8(%a6), %d2
        unlk    %a6
        rts

| select(payload, item): toggle
ds_select:
        lea     ds_on, %a1
        moveq   #0, %d0
        tst.b   (%a1, %d1.l)
        seq     %d0
        andi.l  #1, %d0
        bra     ds_set

| change(payload, item, delta): RIGHT on, LEFT off
ds_change:
        move.l  12(%sp), %d0
        beq.s   9f
        sgt     %d0
        andi.l  #1, %d0
        bra     ds_set
9:      rts

| ds_set: d0 = 0 or 1, d1 = the row; the caller's frame (payload at 4(sp))
| untouched. Keeps the value in the project and core's switch, marks the
| project edited (a load asks first, as after a stock edit), then redraws
| the menu.
ds_set:
        lea     ds_on, %a1
        cmp.b   (%a1, %d1.l), %d0
        beq.s   1f                      | no change: nothing to keep
        move.b  %d0, (%a1, %d1.l)
        move.l  %d0, -(%sp)             | value
        bsr     ds_entry
        move.l  (%sp), %d0
        movea.l (%a1), %a0
        move.b  %d0, (%a0)              | core's switch
        clr.l   -(%sp)                  | index: unused
        clr.l   -(%sp)                  | item: unused (project scope)
        move.l  4(%a1), -(%sp)          | the field's descriptor
        jsr     core_store_set
        lea     16(%sp), %sp
        jsr     core_project_edited
1:      movea.l 4(%sp), %a0
        movea.l (%a0), %a0              | the menu
        lea     0x38(%a0), %a0
        move.l  %a0, 4(%sp)
        jmp     INVALIDATE

| draw(payload, item, bmp, x, y): the checkbox
ds_draw:
        lea     ds_on, %a1
        moveq   #0, %d0
        tst.b   (%a1, %d1.l)
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
| too) and CREATE NEW, the project's choices go into core's switches. A
| project saved without a field, or by another layout, reads 0: off.
        .globl  ds_project
ds_project:
        move.l  %d2, -(%sp)
        moveq   #0, %d2
1:      clr.l   -(%sp)                  | index: unused
        clr.l   -(%sp)                  | item: unused
        move.l  %d2, %d1
        bsr     ds_entry
        move.l  4(%a1), -(%sp)
        jsr     core_store_get
        lea     12(%sp), %sp
        tst.l   %d0
        sne     %d0
        andi.l  #1, %d0
        lea     ds_on, %a0
        move.b  %d0, (%a0, %d2.l)
        move.l  %d2, %d1
        move.l  %d0, -(%sp)
        bsr     ds_entry
        move.l  (%sp)+, %d0
        movea.l (%a1), %a0
        move.b  %d0, (%a0)
        addq.l  #1, %d2
        moveq   #NROWS, %d0
        cmp.l   %d0, %d2
        blt.s   1b
        move.l  (%sp)+, %d2
        rts

        .balign 4
row_dsp:    .long   ds_label_dsp, ds_open, ds_icon, ds_nothing
rows:       .long   row_0, row_1, row_2, row_3, row_4
row_0:      .long   ds_label_0, ds_select_0, ds_draw_0, ds_change_0
row_1:      .long   ds_label_1, ds_select_1, ds_draw_1, ds_change_1
row_2:      .long   ds_label_2, ds_select_2, ds_draw_2, ds_change_2
row_3:      .long   ds_label_3, ds_select_3, ds_draw_3, ds_change_3
row_4:      .long   ds_label_4, ds_select_4, ds_draw_4, ds_change_4
| per row: core's want byte, the project field, the label
ds_rows:    .long   core_voice_skip_want,   ah_cpu_save,      str_voice
            .long   core_sample_skip_want,  ah_sample_skip,   str_sample
            .long   core_fx_skip_want,      ah_fx_skip,       str_fx
            .long   core_reverb_clear_want, ah_reverb_clear,  str_reverb
            .long   core_ui_skip_want,      ah_ui_skip,       str_ui
| the row's icon, a load gauge, 9 x 9 in stock's Bitmap layout (blit
| 0x400716e0): +4 width, +8 height, +12 longs per column, +16 the pixels
| and +20 the mask, a long per column, its bottom row in bit 31 (bitmaps
| run bottom-up: drawn upright, the art below is rows 8..0 from bit 23).
|   .........
|   ..#####..
|   .#.....#.
|   #......##
|   #.....#.#
|   #....#..#
|   #...#...#
|   #########
|   .........
icon_load:  .long   0, 9, 9, 1, icon_bits, icon_mask, 0
icon_bits:  .long   0x7c000000, 0x42000000, 0x41000000, 0x41000000, 0x61000000, 0x51000000, 0x49000000, 0x46000000, 0x7c000000
icon_mask:  .long   0xff800000, 0xff800000, 0xff800000, 0xff800000, 0xff800000, 0xff800000, 0xff800000, 0xff800000, 0xff800000
str_dsp:    .asciz  "DSP SETTINGS"
str_head:   .asciz  "DSP"                   | the header: short, as stock's are (AUDIO ROUTING opens ROUTING)
str_voice:  .asciz  "VOICE SKIP"
str_sample: .asciz  "SAMPLE SKIP"
str_fx:     .asciz  "FX SKIP"
str_reverb: .asciz  "REVERB CLEAR"
str_ui:     .asciz  "UI SKIP"
        .balign 4

| struct core_submenu (core-ar2 0.14, analog-rytm-mk2/settings.h); core
| writes its last three words (.run is RAM)
ds_menu:    .long   str_head, rows, NROWS, 0
            .long   0, 0, 0                 | core's

        .section .bss
ds_on:      .skip 8                 | the rows' states (bytes 0..4), set by ds_project at boot
