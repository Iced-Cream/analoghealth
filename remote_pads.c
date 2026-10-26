/* SPDX-License-Identifier: GPL-2.0-or-later */
#include "remote_pads.h"
#include "core_candidate.h"

/* The OS's panel receiver. The panel's UART interrupt hands it one byte at a
 * time; a message is a header (high nibble type, low nibble index) and a data
 * byte. panel_rx (remote_pads.s) sits at its entry: it logs the byte and marks
 * the receiver busy while it runs. rp_stock_parser is the receiver itself. */
extern void rp_stock_parser(unsigned byte);
extern volatile unsigned char rp_in_panel;
extern volatile unsigned char fw_panel_idle;   /* 1: the next byte is a header */
extern volatile unsigned fw_pad_threshold;     /* where a strike starts to sound */
extern volatile unsigned fw_ui_queue[];
extern unsigned rk_now(void);
extern unsigned core_keys_lock(void);
extern void core_keys_unlock(unsigned sr);

enum { PAD_TICKS_PER_MS = 132000, PAD_GRACE_MS = 2000 };

/* A ring of scripts: head is the one playing, queued counts it and those
 * waiting behind it. Each has its start time on the bus clock. */
static unsigned char script[RP_QUEUE][RP_MAX_EVENTS * 4];
static unsigned script_events[RP_QUEUE], script_start[RP_QUEUE], head, queued;
static unsigned pad_next, pad_state, pad_result, pad_failed;
static unsigned struck, keyed, bridged;        /* pads down, one bit each */

static unsigned log_time[RP_LOG];
static unsigned char log_byte[RP_LOG], log_flag[RP_LOG];
static unsigned logged, capturing;

static void log_put(unsigned byte, unsigned flag)
{
    unsigned at = logged % RP_LOG;
    log_time[at] = rk_now();
    log_byte[at] = byte;
    log_flag[at] = flag;
    ++logged;
}

/* Log flags: bit 0 the byte is ours (a script's), bit 1 the receiver took it
 * as a header (it was between messages). */
enum { LOG_OURS = 1, LOG_HEADER = 2 };

/* panel_rx: every byte the panel sends, in its interrupt. */
void rp_panel_byte(unsigned byte)
{
    if (capturing)
        log_put(byte & 0xff, fw_panel_idle == 1 ? LOG_HEADER : 0);
}

unsigned rp_capture(unsigned mode)
{
    unsigned sr = core_keys_lock(), n;
    if (mode == 1) {
        logged = 0;
        capturing = 1;
    } else if (mode == 0) {
        capturing = 0;
    }
    n = logged;
    core_keys_unlock(sr);
    return n;
}

static void pad_put32(unsigned char *p, unsigned v)
{
    p[0] = v >> 24; p[1] = v >> 16; p[2] = v >> 8; p[3] = v;
}

/* u32 logged, u32 first, u8 n, n x {u32 DTIM0 time, u8 byte, u8 flag}. */
unsigned rp_read(unsigned from, unsigned char *out)
{
    unsigned sr = core_keys_lock(), first, n, i, at;
    first = logged > RP_LOG && from < logged - RP_LOG ? logged - RP_LOG : from;
    n = first < logged ? logged - first : 0;
    if (n > RP_READ_MAX) n = RP_READ_MAX;
    pad_put32(out, logged);
    pad_put32(out + 4, first);
    out[8] = n;
    for (i = 0; i < n; ++i) {
        at = (first + i) % RP_LOG;
        pad_put32(out + 9 + 6 * i, log_time[at]);
        out[13 + 6 * i] = log_byte[at];
        out[14 + 6 * i] = log_flag[at];
    }
    core_keys_unlock(sr);
    return 9 + 6 * n;
}

unsigned rp_threshold(void)
{
    return fw_pad_threshold & 0xff;
}

/* Times rise and stay within RP_MAX_MS; headers name a pad message. */
int rp_valid(const unsigned char *e, unsigned count)
{
    unsigned i, at, last = 0, type;
    if (!count || count > RP_MAX_EVENTS)
        return 0;
    for (i = 0; i < count; ++i, e += 4) {
        at = (unsigned)e[0] << 8 | e[1];
        type = e[2] >> 4;
        if (at < last || at > RP_MAX_MS || (e[2] & 15) >= RP_PADS ||
            !(type == 0 || type == 9 || type == 0xa || type == 0xb || type == 0xc))
            return 0;
        last = at;
    }
    return 1;
}

static unsigned last_ms(unsigned slot)
{
    unsigned n = script_events[slot];
    return n ? (unsigned)script[slot][4 * n - 4] << 8 | script[slot][4 * n - 3] : 0;
}

static void put(unsigned slot, const unsigned char *e, unsigned count, unsigned start)
{
    unsigned i;
    for (i = 0; i < count * 4; ++i)
        script[slot][i] = e[i];
    script_events[slot] = count;
    script_start[slot] = start;
}

void rp_start(const unsigned char *e, unsigned count, unsigned now)
{
    head = 0;
    queued = 1;
    put(0, e, count, now);
    pad_next = pad_result = pad_failed = 0;
    pad_state = RP_RUNNING;
}

/* PAD NEXT: a script that starts after_ms after the start of the last one
 * held, so a chain keeps the take's timing whenever the host sends it. Full
 * while the ring is, or while the script would start more than RP_MAX_MS
 * from now (start times stay well within the clock's signed range); ended
 * once the last script has played out and let go. */
int rp_queue(const unsigned char *e, unsigned count, unsigned after_ms, unsigned now)
{
    unsigned last = (head + queued - 1) & (RP_QUEUE - 1);
    if (pad_state != RP_RUNNING || pad_failed)
        return RP_ENDED;
    if (queued == RP_QUEUE ||
        (int)(script_start[last] - now) > (int)((RP_MAX_MS - after_ms) * PAD_TICKS_PER_MS))
        return RP_FULL;
    put((head + queued) & (RP_QUEUE - 1), e, count, script_start[last] + after_ms * PAD_TICKS_PER_MS);
    ++queued;
    return RP_QUEUED;
}

void rp_abort(void)
{
    if (pad_state == RP_RUNNING) {
        pad_failed = 1;
        pad_state = RP_RELEASING;
    }
    queued = 1;
}

unsigned rp_state(void)
{
    return pad_state;
}

int rp_take_result(void)
{
    int r = pad_result;
    pad_result = 0;
    return r;
}

/* The receiver can take a message: not in the middle of one of the panel's,
 * not running, and the UI's queue has room (as for the bridge's keys). */
static int ready(void)
{
    unsigned count = fw_ui_queue[1], capacity = fw_ui_queue[4] + 1;
    return !rp_in_panel && fw_panel_idle == 1 &&
           capacity >= 16 && count <= capacity - 16 && count <= 16;
}

static void send(unsigned header, unsigned data)
{
    if (capturing) {
        log_put(header, LOG_OURS | LOG_HEADER);
        log_put(data, LOG_OURS);
    }
    rp_stock_parser(header);
    rp_stock_parser(data);
}

/* One event; 0 when it has to wait. Interrupts are masked. */
static int play(unsigned header, unsigned data)
{
    unsigned pad = header & 15, bit = 1u << pad, type = header >> 4;
    int r;
    if (type == 0) {
        r = rk_pad_key(pad, data != 0);
        if (r == CORE_KEYS_BUSY) return 0;
        if (r != CORE_KEYS_OK) { pad_failed = 1; return 1; }
        bridged = data ? bridged | bit : bridged & ~bit;
        return 1;
    }
    if (!ready()) return 0;
    send(header, data);
    if (type == 0xa) struck |= bit;
    else if (type == 0xb) struck &= ~bit;
    else if (type == 0xc) keyed = data ? keyed | bit : keyed & ~bit;
    return 1;
}

/* A pad pressed as a key: a panel press is a strike (with the force the panel
 * measures) as well as the key bit, and the OS takes a pad as held, for PERF or
 * SCNE + pad edits, from the strike. Interrupts are masked. */
static unsigned key_struck;

int rp_key_strike(unsigned pad, unsigned down)
{
    unsigned bit = 1u << pad;
    if (pad >= RP_PADS || !(key_struck & bit) != !!down)
        return 1;                               /* already so */
    if (!ready()) return 0;
    send(down ? 0xa0 | pad : 0xb0 | pad, down ? RP_KEY_FORCE : 0);
    key_struck = down ? key_struck | bit : key_struck & ~bit;
    return 1;
}

unsigned rp_key_struck(void)
{
    return key_struck;
}

/* Let go of every pad the script left down: release, key up, bridge bit. */
static void release_all(void)
{
    unsigned pad;
    for (pad = 0; pad < RP_PADS; ++pad) {
        if ((struck >> pad & 1) && !play(0xb0 | pad, 0)) return;
        if ((keyed >> pad & 1) && !play(0xc0 | pad, 0)) return;
        if ((bridged >> pad & 1) && !play(pad, 0)) return;
    }
    if (struck | keyed | bridged)
        return;
    pad_result = pad_failed ? 2 : 1;
    pad_state = RP_IDLE;
}

static void service(unsigned now)
{
    const unsigned char *e;
    int elapsed;
    while (pad_state == RP_RUNNING) {
        elapsed = (int)(now - script_start[head]);
        if (elapsed < 0)
            return;                         /* a queued script, not due yet */
        if (elapsed >= (int)((last_ms(head) + PAD_GRACE_MS) * PAD_TICKS_PER_MS)) {
            pad_failed = 1;                 /* the receiver stayed busy */
            pad_state = RP_RELEASING;
            break;
        }
        while (pad_next < script_events[head]) {
            e = script[head] + 4 * pad_next;
            if (elapsed < (int)(((unsigned)e[0] << 8 | e[1]) * PAD_TICKS_PER_MS) || !play(e[2], e[3]))
                return;
            ++pad_next;
        }
        if (pad_failed || queued == 1) {
            pad_state = RP_RELEASING;
            break;
        }
        /* The next script carries on: the pads stay down across the join. */
        head = (head + 1) & (RP_QUEUE - 1);
        --queued;
        pad_next = 0;
    }
    if (pad_state == RP_RELEASING)
        release_all();
}

/* ev_render_in: 1500 times a second, so a script keeps its timing to 0.7 ms. */
void rp_render(void)
{
    unsigned sr;
    if (pad_state == RP_IDLE)
        return;
    sr = core_keys_lock();
    service(rk_now());
    core_keys_unlock(sr);
}

/* From the UI tick during a cancel, with the lock held. */
void rp_flush(void)
{
    if (pad_state != RP_IDLE)
        service(rk_now());
}
