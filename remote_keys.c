/* SPDX-License-Identifier: GPL-2.0-or-later */
#include "remote_keys.h"
#include "core_candidate.h"
#ifdef RK_PADS
#include "remote_pads.h"
#define RK_SCRIPT_MAX RP_MAX_EVENTS
#else
#define RK_SCRIPT_MAX 0
#endif

#ifndef RK_OS_MINOR
#error "RK_OS_MINOR must identify the OS port"
#endif
#ifndef RK_PRODUCT
#error "RK_PRODUCT must identify the device"
#endif

/* Each logical key's OS key code. Its panel group and bit come from the
 * unit's own slot table at run time (locate), so no table ships here. */
static const unsigned char codes[RK_KEY_COUNT + 1] = {
#define KEY(id, name, code) [id] = code,
#if RK_PRODUCT == 3
#include "keys-ar2.def"
#else
#include "keys-a4.def"
#endif
#undef KEY
};
static struct key_position { unsigned char group, mask; } keymap[RK_KEY_COUNT + 1];
static unsigned located;

/* The OS's slot table: one key code per panel slot, slot = 8 group + bit
 * (core_candidate.s: fw_key_table). The Rytm's driver reads it through a
 * pointer, the A4's directly. */
#ifdef RK_KEY_TABLE_DIRECT
extern const unsigned fw_key_table[];
#else
extern const unsigned *fw_key_table;
#endif

static void locate(void)
{
    unsigned id, slot;
    if (located)
        return;
    for (id = 1; id <= RK_KEY_COUNT; ++id)
        for (slot = 0; slot < CORE_KEYS_GROUPS * 8; ++slot)
            if (fw_key_table[slot] == codes[id]) {
                keymap[id].group = slot / 8;
                keymap[id].mask = 1u << (slot % 8);
                break;
            }
    located = 1;
}

/* The REMOTE KEYS row (sysinfo.s): nonzero when the user turned it off. */
extern volatile unsigned char rk_off;

extern unsigned rk_now(void);
extern unsigned core_keys_lock(void);
extern void core_keys_unlock(unsigned sr);

enum { TICKS_PER_MS = 132000, MAX_HOLD_MS = 2000, MAX_TAP_MS = 250 };
static unsigned session, operation, action, key, duration, state, reason;
static unsigned char masks[CORE_KEYS_GROUPS];
static unsigned held_key, held_start, held_ticks, tap_start;
static unsigned started, tapping, cleanup, closed, turns_left;

static unsigned get32(const unsigned char *p)
{
    return (unsigned)p[0] << 24 | (unsigned)p[1] << 16 | (unsigned)p[2] << 8 | p[3];
}

static void put32(unsigned char *p, unsigned value)
{
    p[0] = value >> 24; p[1] = value >> 16; p[2] = value >> 8; p[3] = value;
}

static void put16(unsigned char *p, unsigned value)
{
    p[0] = value >> 8; p[1] = value;
}

static unsigned owned(void)
{
    unsigned i;
    for (i = 0; i < CORE_KEYS_GROUPS; ++i)
        if (masks[i]) return 1;
    return 0;
}

/* 128 bits, big-endian: bit (logical ID - 1). */
static void bitmap(unsigned char *p, unsigned supported)
{
    unsigned i;
    for (i = 0; i < RK_BITMAP; ++i) p[i] = 0;
    for (i = 1; i <= RK_KEY_COUNT; ++i)
        if (keymap[i].mask && (supported || (masks[keymap[i].group] & keymap[i].mask)))
            p[RK_BITMAP - 1 - (i - 1) / 8] |= 1u << ((i - 1) % 8);
}

/* Exact canonical packing prevents an oversized request becoming a valid prefix. */
static int unpack(const unsigned char *src, unsigned length, unsigned char *dst, unsigned count)
{
    unsigned at = 0, out = 0, n, i, header;
    if (length != count + (count + 6) / 7)
        return 0;
    while (out < count) {
        n = count - out;
        if (n > 7) n = 7;
        header = src[at++];
        if (header & 0x80 || header & ((1u << (7 - n)) - 1))
            return 0;
        for (i = 0; i < n; ++i) {
            if (src[at] & 0x80) return 0;
            dst[out++] = src[at++] | ((header << (i + 1)) & 0x80);
        }
    }
    return 1;
}

static int report(unsigned char *reply)
{
    put32(reply + 4, session);
    put32(reply + 8, operation);
    reply[12] = state;
    reply[13] = reason;
    bitmap(reply + 14, 0);
    reply[14 + RK_BITMAP] = !!cleanup;
    return 15 + RK_BITMAP;
}

#ifdef RK_PADS
/* A pad's key bit through the bridge, kept in the session's keys so a cancel
 * lets go of it too. Called by the pad player with the lock held. */
int rk_pad_key(unsigned pad, unsigned down)
{
    unsigned id, group, target;
    int r;
    for (id = 1; id <= RK_KEY_COUNT && codes[id] != 0x90 + pad; ++id)
        ;                                       /* BD..CB are codes 0x90..0x9b */
    if (id > RK_KEY_COUNT || !keymap[id].mask) return CORE_KEYS_INVALID;
    group = keymap[id].group;
    target = down ? masks[group] | keymap[id].mask : masks[group] & ~keymap[id].mask;
    r = core_keys_set(group, target);
    if (r == CORE_KEYS_OK) masks[group] = target;
    return r;
}

/* PAD CAPS, CAPTURE and CAPTURE READ observe; PAD RUN is one operation of the
 * key session, as an ACTION is, and PAD NEXT one that follows the script
 * before it (docs/PADS.md). */
static int pad_receive(unsigned command, const unsigned char *packed, unsigned length,
                       unsigned char *reply)
{
    static unsigned char request[16 + 4 * RP_MAX_EVENTS];
    const unsigned char *events = request + 12;
    unsigned count, n = 0, owner, number, after = 0, fixed, sr;
    int result = 4, queuing;
    reply[0] = packed[1] | ((packed[0] << 1) & 0x80);
    reply[1] = packed[2] | ((packed[0] << 2) & 0x80);
    reply[2] = command | 0x80;
    reply[3] = RK_INVALID;
    if (command == RK_PAD_RUN || command == RK_PAD_NEXT) {
        fixed = command == RK_PAD_RUN ? 12 : 14;
        for (n = 1; n <= RP_MAX_EVENTS; ++n) {
            count = fixed + 4 * n;
            if (length == count + (count + 6) / 7) break;
        }
        if (n > RP_MAX_EVENTS) return 4;
    } else {
        count = command == RK_PAD_CAPS ? 3 : command == RK_CAPTURE ? 4 : 7;
    }
    if (!unpack(packed, length, request, count)) return 4;
    reply[3] = RK_OK;
    if (command == RK_PAD_CAPS) {
        put16(reply + 4, RP_REVISION);
        reply[6] = RP_PADS; reply[7] = RP_MAX_EVENTS;
        put16(reply + 8, RP_MAX_MS);
        put16(reply + 10, RP_LOG);
        reply[12] = rp_threshold();
        reply[13] = !rk_off;
        reply[14] = RP_QUEUE - 1;
        return 15;
    }
    if (command == RK_CAPTURE) {
        if (request[3] > 2) { reply[3] = RK_INVALID; return 4; }
        put32(reply + 4, rp_capture(request[3]));
        return 8;
    }
    if (command == RK_CAPTURE_READ)
        return 4 + rp_read(get32(request + 3), reply + 4);
    if (rk_off) { reply[3] = RK_OFF; return 4; }
    owner = get32(request + 3);
    number = get32(request + 7);
    if (command == RK_PAD_NEXT) {
        after = (unsigned)request[11] << 8 | request[12];
        events = request + 14;
    }
    sr = core_keys_lock();
    /* events[-1]: the event count, just before the events */
    if (!owner || !number || events[-1] != n || !rp_valid(events, n) || after > RP_MAX_MS) {
        reply[3] = RK_INVALID;
    } else if (owner != session) {
        reply[3] = RK_STALE;
    } else if (number == operation) {
        if (action != RK_PAD) reply[3] = RK_INVALID;   /* a repeat: the latest report */
    } else if (closed || number < operation || operation == 0xffffffffu || number != operation + 1) {
        reply[3] = RK_STALE;
    } else if (command == RK_PAD_NEXT) {
        /* Stale once the script before it has played out: the host starts
         * the next one with PAD RUN. */
        queuing = cleanup || action != RK_PAD || state != RK_ACCEPTED
                  ? RP_ENDED : rp_queue(events, n, after, rk_now());
        if (queuing == RP_QUEUED) operation = number;
        else reply[3] = queuing == RP_FULL ? RK_BUSY : RK_STALE;
    } else if (cleanup || state == RK_ACCEPTED || owned() || rp_state() != RP_IDLE) {
        reply[3] = RK_BUSY;
    } else {
        operation = number; action = RK_PAD; key = 0; duration = 0;
        state = RK_ACCEPTED; reason = RK_OK; tapping = 0;
        started = rk_now();
        rp_take_result();
        rp_start(events, n, started);
    }
    if (reply[3] == RK_OK) result = report(reply);
    core_keys_unlock(sr);
    return result;
}
#endif

int rk_receive(const unsigned char *packed, unsigned length, unsigned char *reply)
{
    static unsigned char request[16 + 4 * RK_SCRIPT_MAX];
    unsigned command, count, owner, number, a, k, ms, sr;
    int result = 4;
    if (length < 4) return 0;
    locate();
    command = packed[3] | ((packed[0] << 3) & 0x80);
#ifdef RK_PADS
    if (command >= RK_PAD_CAPS && command <= RK_PAD_NEXT)
        return pad_receive(command, packed, length, reply);
#endif
    if (command < RK_CAPS || command > RK_CANCEL) return 0;
    reply[0] = packed[1] | ((packed[0] << 1) & 0x80);
    reply[1] = packed[2] | ((packed[0] << 2) & 0x80);
    reply[2] = command | 0x80;
    reply[3] = RK_INVALID;
    count = command == RK_CAPS ? 3 : command == RK_ACTION ? 15 : command == RK_STATUS ? 11 : 7;
    if (!unpack(packed, length, request, count)) return 4;
    reply[3] = RK_OK;
    if (rk_off) {
        reply[3] = RK_OFF;
        return 4;
    }
    if (command == RK_CAPS) {
        put16(reply + 4, RK_REVISION);
        reply[6] = RK_PRODUCT; reply[7] = 1; reply[8] = RK_OS_MINOR;
        bitmap(reply + 9, 1);
        put16(reply + 9 + RK_BITMAP, 100);
        put16(reply + 11 + RK_BITMAP, MAX_TAP_MS);
        put16(reply + 13 + RK_BITMAP, MAX_HOLD_MS);
        reply[15 + RK_BITMAP] = RK_ENCODERS;
        return 16 + RK_BITMAP;
    }
    owner = get32(request + 3);
    sr = core_keys_lock();
    if (!owner) {
        reply[3] = RK_INVALID;
    } else if (command == RK_OPEN) {
        if (owner == session) {
            if (closed) reply[3] = RK_STALE;
        } else if (owned() || cleanup || state == RK_ACCEPTED) {
            reply[3] = RK_BUSY;
        } else {
            session = owner;
            operation = action = key = duration = reason = state = closed = 0;
        }
    } else if (owner != session) {
        reply[3] = RK_STALE;
    } else if (command == RK_STATUS) {
        number = get32(request + 7);
        if (number && number != operation) reply[3] = RK_STALE;
    } else if (command == RK_CANCEL) {
        if (!closed) {
            cleanup = RK_CANCELLED;
            state = RK_ACCEPTED;
        }
    } else {
        number = get32(request + 7);
        a = request[11]; k = request[12];
        ms = (unsigned)request[13] << 8 | request[14];
        /* TURN: key = encoder 1..9; duration = signed step per tick << 8 | ticks. */
        if (!number || a < RK_PRESS || a > RK_TURN ||
            (a == RK_TURN ? k < 1 || k > RK_ENCODERS || !(ms & 0xff) || (ms & 0xff) > RK_MAX_TURNS ||
                            !(ms >> 8) || (signed char)(ms >> 8) > RK_MAX_STEP ||
                            (signed char)(ms >> 8) < -RK_MAX_STEP
                          : k < 1 || k > RK_KEY_COUNT || !keymap[k].mask ||
                            (a == RK_RELEASE ? ms != 0
                                             : !ms || ms > (a == RK_PRESS ? MAX_HOLD_MS : MAX_TAP_MS)))) {
            reply[3] = RK_INVALID;
        } else if (number == operation) {
            if (a != action || k != key || ms != duration) reply[3] = RK_INVALID;
        } else if (closed || number < operation || operation == 0xffffffffu || number != operation + 1) {
            reply[3] = RK_STALE;
        } else if (cleanup || state == RK_ACCEPTED || (a == RK_PRESS && held_key && held_key != k)) {
            reply[3] = RK_BUSY;
        } else if (a == RK_TAP && (masks[keymap[k].group] & keymap[k].mask)) {
            reply[3] = RK_OVERLAP;
        } else {
            operation = number; action = a; key = k; duration = ms;
            state = RK_ACCEPTED; reason = RK_OK; tapping = 0;
            turns_left = a == RK_TURN ? ms & 0xff : 0;
            started = rk_now();
        }
    }
    if (reply[3] == RK_OK) result = report(reply);
    core_keys_unlock(sr);
    return result;
}

void rk_tick(void *ctrl)
{
    unsigned sr = core_keys_lock(), now = rk_now(), target, group;
    int result;
    (void)ctrl;
    locate();
    /* shortcut: a >32 s UI stall can wrap DTIM0; use a wider clock if required. */
    if ((held_key && now - held_start >= held_ticks) ||
        (state == RK_ACCEPTED && !tapping && action != RK_PAD &&
         now - started >= MAX_HOLD_MS * TICKS_PER_MS))
        cleanup = RK_EXPIRED;
    /* Turned off: let go of what the session holds or is about to do. */
    if (rk_off && !cleanup && (owned() || state == RK_ACCEPTED))
        cleanup = RK_CANCELLED;
#ifdef RK_PADS
    /* The pad player stops itself (rp_render); a cancel stops it here and
     * waits until it has let go of every pad. */
    if (cleanup && rp_state() != RP_IDLE) {
        rp_abort();
        rp_flush();
        if (rp_state() != RP_IDLE) {
            core_keys_unlock(sr);
            return;
        }
        rp_take_result();
    }
    if (!cleanup && state == RK_ACCEPTED && action == RK_PAD) {
        result = rp_take_result();
        if (result) {
            state = result == 1 ? RK_COMPLETED : RK_FAILED;
            reason = result == 1 ? RK_OK : RK_BUSY;
        }
        core_keys_unlock(sr);
        return;
    }
#endif
    if (cleanup) {
        for (group = 0; group < CORE_KEYS_GROUPS; ++group) {
            if (core_keys_set(group, 0) != CORE_KEYS_OK) break;
            masks[group] = 0;
        }
        if (group == CORE_KEYS_GROUPS) {
            held_key = tapping = 0;
            state = cleanup; cleanup = 0; closed = 1; reason = RK_OK;
        }
    } else if (state == RK_ACCEPTED) {
        if (action == RK_TURN) {
            /* One turn message a UI tick, so a burst arrives as the panel's do. */
            result = core_encoder_turn(key - 1, (signed char)(duration >> 8));
            if (result == CORE_KEYS_OK) {
                if (!--turns_left) state = RK_COMPLETED;
            } else if (result != CORE_KEYS_BUSY) {
                state = RK_FAILED;
                reason = RK_INVALID;
            }
            core_keys_unlock(sr);
            return;
        }
        if (tapping && now - tap_start < duration * TICKS_PER_MS) {
            core_keys_unlock(sr);
            return;
        }
        group = keymap[key].group;
        target = action == RK_RELEASE || tapping ? masks[group] & ~keymap[key].mask
                                                : masks[group] | keymap[key].mask;
        result = core_keys_set(group, target);
        if (result == CORE_KEYS_OK) {
            masks[group] = target;
            if (action == RK_PRESS) {
                if (!held_key) {
                    held_key = key; held_start = now; held_ticks = duration * TICKS_PER_MS;
                }
                state = RK_COMPLETED;
            } else if (action == RK_RELEASE) {
                if (held_key == key) held_key = 0;
                state = RK_COMPLETED;
            } else if (!tapping) {
                tapping = 1; tap_start = now;
            } else {
                tapping = 0; state = RK_COMPLETED;
            }
        } else if (result != CORE_KEYS_BUSY) {
            state = RK_FAILED;
            reason = result == CORE_KEYS_OVERLAP ? RK_OVERLAP : RK_INVALID;
        }
    }
    core_keys_unlock(sr);
}
