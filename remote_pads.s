| SPDX-License-Identifier: GPL-2.0-or-later
| The panel receiver's entry (site PANEL_RX, 8 bytes, a jmp here). Called by
| the panel's UART interrupt with one byte at 4(sp).
        .section .run, "ax"
        .globl  panel_rx, rp_stock_parser, rp_in_panel
        .globl  fw_panel_idle, fw_pad_threshold
        .set    fw_panel_idle, PANEL_IDLE
        .set    fw_pad_threshold, PAD_THRESHOLD
panel_rx:
        moveq   #1, %d0
        move.b  %d0, rp_in_panel
        move.l  4(%sp), -(%sp)
        jsr     rp_panel_byte
        addq.l  #4, %sp
        move.l  4(%sp), -(%sp)
        bsr.w   rp_stock_parser
        addq.l  #4, %sp
        clr.b   rp_in_panel
        rts

| The receiver as stock: the prologue the jmp displaced, then the rest.
rp_stock_parser:
        lea     -28(%sp), %sp
        movem.l %d2-%d6/%a2-%a3, (%sp)
        jmp     PANEL_CONTINUE

        .section .bss
rp_in_panel:    .skip 4
