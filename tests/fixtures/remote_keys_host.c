/* SPDX-License-Identifier: GPL-2.0-or-later */
#include <assert.h>
#include <string.h>

volatile unsigned char fw_key_state[16];
volatile unsigned fw_ui_queue[8];
volatile unsigned char rk_off;
static unsigned clock_ticks, depth, calls;
static unsigned fill_after_group;

unsigned core_keys_lock(void) { return depth++; }
void core_keys_unlock(unsigned sr) { assert(depth && --depth == sr); }
void core_stock_buttons(unsigned group, unsigned mask)
{
    assert(depth);
    ++calls;
    fw_key_state[group] = mask;
    if (!mask && group == fill_after_group) {
        fw_ui_queue[1] = 17;
        fill_after_group = 99;
    }
}
unsigned rk_now(void) { return clock_ticks; }
static int turned[16], turn_calls;
void fw_encoder(unsigned encoder, int delta) { assert(depth); turned[encoder] += delta; ++turn_calls; }

/* A synthetic slot table (not the OS's): logical key i sits in slot 7i mod
 * (8 x groups), every other slot holds 0xff, which no key uses. */
static unsigned host_table[16 * 8];
const unsigned *fw_key_table = host_table;

#ifndef RK_PRODUCT
#define RK_PRODUCT 3
#endif
#if RK_PRODUCT == 3
#define RK_OS_MINOR 74
#else
#define RK_OS_MINOR 56
#endif
#include "../../core_candidate.c"
#include "../../remote_keys.c"

void host_reset(void)
{
    unsigned i;
    assert(!depth);
    for (i = 0; i < 16 * 8; ++i) host_table[i] = 0xff;
    for (i = 1; i <= RK_KEY_COUNT; ++i) host_table[7 * i % (CORE_KEYS_GROUPS * 8)] = codes[i];
    memset(keymap, 0, sizeof(keymap));
    located = 0;
    memset(turned, 0, sizeof(turned));
    turn_calls = 0;
    turns_left = 0;
    memset((void *)fw_key_state, 0, sizeof(fw_key_state));
    memset((void *)fw_ui_queue, 0, sizeof(fw_ui_queue));
    memset(physical, 0, sizeof(physical));
    memset(synthetic, 0, sizeof(synthetic));
    fw_ui_queue[4] = 255;
    initialized = clock_ticks = calls = 0;
    fill_after_group = 99;
    session = operation = action = key = duration = state = reason = 0;
    memset(masks, 0, sizeof(masks));
    rk_off = 0;
    held_key = held_start = held_ticks = tap_start = 0;
    started = tapping = cleanup = closed = 0;
}
void host_advance(unsigned ms) { clock_ticks += ms * TICKS_PER_MS; rk_tick(0); }
void host_clock(unsigned ticks) { clock_ticks = ticks; }
void host_busy(unsigned busy) { fw_ui_queue[1] = busy ? 17 : 0; }
unsigned host_calls(void) { return calls; }
void host_physical_group(unsigned group, unsigned mask) { core_panel_buttons(group, mask); }
unsigned host_group(unsigned group) { return fw_key_state[group]; }
void host_fill_after(unsigned group) { fill_after_group = group; }
void host_off(unsigned off) { rk_off = off; }
unsigned host_key_group(unsigned id) { locate(); return keymap[id].group; }
unsigned host_key_mask(unsigned id) { locate(); return keymap[id].mask; }
void host_unmap(unsigned id) { host_table[7 * id % (CORE_KEYS_GROUPS * 8)] = 0xff; }
unsigned host_slots(void) { return CORE_KEYS_GROUPS * 8; }
unsigned host_product(void) { return RK_PRODUCT; }
int host_turned(unsigned encoder) { return turned[encoder]; }
unsigned host_turn_calls(void) { return turn_calls; }
