/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef REMOTE_KEYS_H
#define REMOTE_KEYS_H

enum { RK_CAPS = 0x10, RK_OPEN, RK_ACTION, RK_STATUS, RK_CANCEL };
/* Pads (Analog Rytm MKII): docs/PADS.md. */
enum { RK_PAD_CAPS = 0x18, RK_CAPTURE, RK_CAPTURE_READ, RK_PAD_RUN, RK_PAD_NEXT };
enum { RK_PRESS = 1, RK_RELEASE, RK_TAP, RK_TURN, RK_PAD };
enum { RK_NONE, RK_ACCEPTED, RK_COMPLETED, RK_CANCELLED, RK_EXPIRED, RK_FAILED };
enum { RK_OK, RK_INVALID, RK_UNKNOWN, RK_BUSY, RK_STALE, RK_OVERLAP, RK_OFF };
/* Revision 3: digihealth's revision 2 with 128-bit key maps (the Rytm has 78
 * keys, the A4 89). Revision 4 adds TURN, encoder turns, and the encoder count
 * to CAPS. Product 3 is the Analog Rytm MKII, 4 the Analog Four MKII. */
#if RK_PRODUCT == 3
enum { RK_KEY_COUNT = 78, RK_ENCODERS = 9 };          /* A-H, LEVEL */
#define RK_PADS 1
#elif RK_PRODUCT == 4
enum { RK_KEY_COUNT = 89, RK_ENCODERS = 11 };         /* A-J, LEVEL */
#else
#error "RK_PRODUCT must be 3 (Analog Rytm MKII) or 4 (Analog Four MKII)"
#endif
/* Revision 5: up to RK_MAX_HELD keys held at once, each with its own lease,
 * and pad scripts while keys are held. */
enum { RK_REVISION = 5, RK_BITMAP = 16, RK_MAX_TURNS = 30, RK_MAX_STEP = 30, RK_MAX_HELD = 4 };

int rk_receive(const unsigned char *packed, unsigned length, unsigned char *reply);
void rk_tick(void *ctrl);

#endif
