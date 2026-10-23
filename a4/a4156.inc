| SPDX-License-Identifier: GPL-2.0-or-later
| Analog Four MKII MAIN OS 1.56: addresses of the stock routines and data this
| mod calls or reads (the A4's counterparts of the Rytm's in ar174.inc).
        .equ OP_NEW,      0x400653b8    | operator new(size) -> d0
        .equ ITEM_CTOR,   0x4005c7c4    | MenuItem(this, label, select, draw, change, id, step)
        .equ MENU_ADD,    0x4005bffa    | Menu::addItem(menu, item)
        .equ STR_CTOR,    0x4018471e    | std::string(this, const char*, alloc&)
        .equ INVALIDATE,  0x40061a58    | View::invalidate(view)
        .equ BLIT,        0x4005aed4    | blit(dst, src, x, y, centre)
        .equ CHECKBOXES,  0x4186fa58    | -> Bitmap[2], 0x1c each: empty, ticked
        .equ DRAWALL,     0x400625ca    | ViewController::drawAll(ctrl, Bitmap&)
        .equ FILLRECT,    0x40059f1a    | (bmp, x0, y0, x1, y1, colour)
        .equ FRAMERECT,   0x40059cfe    | (bmp, x0, y0, x1, y1, colour), outline
        .equ TEXTF,       0x4005aaf0    | (bmp, rawfont, x, y, maxlen, fmt, ...)
        .equ FONT5,       0x4020774c    | the stock 5-px font (its raw header)
        .equ LOCK,        0x40001cbc    | mutex lock(m)
        .equ UNLOCK,      0x40001dde    | mutex unlock(m)
        .equ HEAPMTX,     0x417df8c8    | the heap's mutex
        .equ HEAPCB,      0x40212338    | the heap's control block
        .equ SYSEX_SEND,  0x40068bd8    | bool (buf, len, abort*, port); 2 = USB
        .equ DTMR0,       0xFC070000    | DMA timer 0 mode
        .equ DTCN0,       0xFC07000C    | DMA timer 0 counter, 132 MHz bus clock
        .equ CUR_TCB,     0x4287c5a4    | the running task's TCB
        .equ IDLE_TCB,    0x41869efc    | the idle task
        .equ IDLE_PC,     0x401062fa    | where it waits
        .equ SYSEX_A2,    0x4019888a    | restored by hook_sysex (a2)
| No sample memory on the A4: SMPFREE is not defined, so the readout's second
| page is RAM alone.
