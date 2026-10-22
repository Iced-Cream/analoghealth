| SPDX-License-Identifier: GPL-2.0-or-later
| Analog Rytm MKII MAIN OS 1.74: addresses of the stock routines and data this
| mod calls or reads (the Rytm's counterparts of digihealth's Digitakt ones).
        .equ OP_NEW,      0x40082a40    | operator new(size) -> d0
        .equ ITEM_CTOR,   0x40072fbc    | MenuItem(this, label, select, draw, change, id, step)
        .equ MENU_ADD,    0x400727f2    | Menu::addItem(menu, item)
        .equ FN_MGR,      0x4019411a    | std::function manager, 4-byte payload
        .equ STR_CTOR,    0x401bcff0    | std::string(this, const char*, alloc&)
        .equ INVALIDATE,  0x40078580    | View::invalidate(view)
        .equ BLIT,        0x400716e0    | blit(dst, src, x, y, centre)
        .equ CHECKBOXES,  0x419f0910    | -> Bitmap[2], 0x1c each: empty, ticked
        .equ DRAWALL,     0x400790f2    | ViewController::drawAll(ctrl, Bitmap&)
        .equ FILLRECT,    0x40070726    | (bmp, x0, y0, x1, y1, colour)
        .equ FRAMERECT,   0x4007050a    | (bmp, x0, y0, x1, y1, colour), outline
        .equ TEXTF,       0x400712fc    | (bmp, rawfont, x, y, maxlen, fmt, ...)
        .equ FONT5,       0x4027aa28    | the stock 5-px font (its raw header)
        .equ LOCK,        0x40001124    | mutex lock(m)
        .equ UNLOCK,      0x40001246    | mutex unlock(m)
        .equ HEAPMTX,     0x418f1350    | the heap's mutex
        .equ HEAPCB,      0x40285b90    | the heap's control block
        .equ SMPFREE,     0x4009d022    | sample memory free bytes -> d0
        .equ SYSEX_SEND,  0x40085a18    | bool (buf, len, abort*, port); 2 = USB
        .equ DTMR0,       0xFC070000    | DMA timer 0 mode
        .equ DTCN0,       0xFC07000C    | DMA timer 0 counter, 132 MHz bus clock
        .equ CUR_TCB,     0x42f67924    | the running task's TCB
        .equ IDLE_TCB,    0x419b2324    | the idle task
        .equ IDLE_PC,     0x40100a7e    | where it waits
        .equ SYSEX_A3,    0x401d7e1e    | restored by hook_sysex (a3)
