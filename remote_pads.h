/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef REMOTE_PADS_H
#define REMOTE_PADS_H

/* Analog Rytm MKII only: the front panel's pad messages, as the panel sends
 * them, fed to the OS's own panel parser; and a log of the bytes the panel
 * sends, to learn what a real hit is. See docs/PADS.md. */
enum { RP_PADS = 12, RP_MAX_EVENTS = 48, RP_MAX_MS = 10000, RP_LOG = 512,
       RP_READ_MAX = 40, RP_REVISION = 2 };
/* Scripts held at once: the one playing and up to three queued behind it
 * (revision 2, PAD NEXT). A power of two: the ring wraps with a mask. */
enum { RP_QUEUE = 4 };
/* Event kinds by header: 0x9p pressure, 0xAp strike, 0xBp release and 0xCp
 * the pad's key as one panel key message (p = pad 0..11); 0x0p, not a panel
 * message, is pad p's key bit through the input bridge. */
enum { RP_IDLE, RP_RUNNING, RP_RELEASING };
enum { RP_QUEUED, RP_FULL, RP_ENDED };    /* rp_queue */

int rp_valid(const unsigned char *events, unsigned count);
void rp_start(const unsigned char *events, unsigned count, unsigned now);
int rp_queue(const unsigned char *events, unsigned count, unsigned after_ms, unsigned now);
void rp_abort(void);
void rp_flush(void);
/* A pad held as a key (PRESS, RELEASE or TAP of BD..CB): the strike or release the
 * panel sends with the key bit. 1 sent (or already so), 0 wait (receiver busy). */
enum { RP_KEY_FORCE = 100 };
int rp_key_strike(unsigned pad, unsigned down);
unsigned rp_key_struck(void);
unsigned rp_state(void);
int rp_take_result(void);           /* 0 running or idle, 1 done, 2 failed */
unsigned rp_threshold(void);
unsigned rp_capture(unsigned mode); /* -> bytes logged so far */
unsigned rp_read(unsigned from, unsigned char *out); /* -> payload length */

/* remote_keys.c: a pad's key bit through the bridge, with the lock held. */
int rk_pad_key(unsigned pad, unsigned down);

#endif
