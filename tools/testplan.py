# SPDX-License-Identifier: GPL-2.0-or-later
"""Test plans: one file, one command, one short verdict.

    python analogrun.py run PLAN.txt [--quiet]

A plan is settings (`key = value`) followed by named steps (`[name]`), each a
list of analogrun steps (see analogrun.py) or audio steps:

    name = SRC hold
    unit = rytm                   # the lock name of the unit (rytm, a4, digitone)
    port = Analog Rytm            # or the emulator's "rytm-emu usb out,rytm-emu usb in", or tcp:HOST:PORT
    din = USB MIDI Interface      # the MIDI interface on the unit's DIN ports, for send/capture ... din
                                  # (analogrun --din wins over it; opened only when a step uses din;
                                  # Rytm and A4 only)
    digirun = C:/path/to/digihealth/tools      # digitone: where digirun.py is (or DIGIRUN_PATH)
    emulator = yes                # an emulator run: no lock, references may be recorded
    record-refs = yes             # (emulator only) expect-screen saves references
    refs = refs                   # folder of reference screens, next to the plan
    out = runs                    # where run folders go (default: runs, next to the plan)
    lock = python C:/path/to/devlock.py        # claim/release command (optional)
    flash = builds/X.syx          # flash this OS first (optional)
    elkflash = C:/path/to/elkflash             # the elkflash checkout
    emulator-run = runs/x-emu-...  # hardware flash: a passing emulator run of this plan
    include = smoke.txt           # another plan's settings and steps; this file's settings win
    audio-tools = C:/path/to/usb-audio         # asio_tone.py, asio_rec.py, audiocheck.py
    audio-python = py -3.14
    audio-driver = Elektron Analog Rytm MKII   # ASIO driver name
    note = left on stock 1.74     # added to the release note

    [boot screen]
    expect-screen home 5          # reference home.bin, waiting up to 5 s
    [pattern audio]
    tap PLAY
    audio-tone 10 tone            # 997 Hz into the unit for 10 s, all inputs to tone.wav
    tap STOP
    audio-dropouts tone 1,2
    audio-compare stock-tone tone 1,2

A step whose name ends in "(hardware)" (audio over ASIO, say) is skipped in an
emulator run. Steps run in order and stop at the first failure (the rest are
skipped). A `wait N` straight before an `expect-screen` is folded into the check, which then
polls for up to N plus its own seconds and passes once the screen matches and holds (not
while recording references).
Everything goes to a run folder (log.txt, screenshots, recordings,
summary.json); the command prints one line per step and the first failure's
detail. A hardware flash needs `emulator-run`: a passing emulator run of the
same plan with the same OS file (sha256), so a build reaches a unit only
after it has passed in the emulator.

A Digitone plan (unit = digitone) runs its steps through digihealth's digirun.py
instead of analogrun.py (no send or save-* steps there). The two share module
names (elkrpc, midisock), so such a plan runs in a process of its own:

    python testplan.py PLAN.txt [--port PORT] [--quiet]
"""
import contextlib
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time

KEY = re.compile(r'^([a-z][a-z0-9-]*)\s*=\s*(.*)$')
PORTS = {'rytm': 'Analog Rytm', 'a4': 'Analog Four', 'digitone': 'Digitone'}
DIGI_UNITS = ('digitone',)               # units whose steps run through digirun.py


def uses_din(steps):
    """Whether any send/capture step ends in din (analogrun's DIN legs)."""
    for _, lines in steps:
        for l in lines:
            w = shlex.split(l, comments=True)
            if w and w[-1].lower() == 'din' and (w[0].lower() == 'send' and len(w) > 2 or
                                                 w[0].lower() == 'capture' and len(w) > 3):
                return True
    return False


def is_plan(path):
    """A plan has settings or [step] headers; a plain steps file has neither."""
    try:
        with open(path, encoding='utf-8') as fh:
            lines = [l.strip() for l in fh if l.strip() and not l.strip().startswith('#')]
    except OSError:
        return False
    return any(l.startswith('[') and l.endswith(']') or re.match(r'include\s*=', l) for l in lines)


def parse(path):
    settings, steps = {}, []
    with open(path, encoding='utf-8') as fh:
        for n, raw in enumerate(fh, 1):
            line = re.sub(r'(^|\s)#.*$', '', raw).strip()     # '#' starts a comment after a space
            if not line:
                continue
            if line.startswith('[') and line.endswith(']'):
                steps.append((line[1:-1].strip(), []))
            elif not steps:
                m = KEY.match(line)
                if not m:
                    raise ValueError('%s:%d: expected key = value before the first [step]' % (path, n))
                settings[m.group(1)] = m.group(2).strip()
            else:
                steps[-1][1].append(line)
    if 'include' in settings:
        # include = OTHER.txt: its settings and steps, with this file's settings on
        # top (e.g. the same plan for the emulator: emulator = yes, port = ...).
        base = settings.pop('include')
        base = base if os.path.isabs(base) else os.path.join(os.path.dirname(os.path.abspath(path)), base)
        inc_settings, inc_steps = parse(base)
        settings = dict(inc_settings, **settings)
        steps = steps or inc_steps
    if not steps:
        raise ValueError('%s: no [step] sections' % path)
    return settings, steps


def merge_wait(line, following):
    """`wait N` straight before `expect-screen REF [T] [rows=..]` (or before `shot X` and
    then that check) -> the lines to run instead of the next one or two: the check polling
    for N + T s and confirming the match holds (`settle`), then the shot if there was one;
    or None. The wait only gave the screen time to settle, and the check already retries,
    so this saves most of N; the shot is then of the same, checked screen."""
    w, nxt = shlex.split(line, comments=True), [shlex.split(l, comments=True) for l in following]
    if len(w) != 2 or w[0].lower() != 'wait' or not nxt or not nxt[0]:
        return None
    shot = []
    if nxt[0][0].lower() == 'shot' and len(nxt) > 1 and nxt[1]:
        shot, nxt = [following[0]], nxt[1:]
    if nxt[0][0].lower() != 'expect-screen':
        return None
    e = nxt[0]
    t = [x for x in e[2:] if not x.startswith('rows=')]
    try:
        total = float(w[1]) + (float(t[0]) if t else 0)
    except ValueError:
        return None
    return [' '.join(['expect-screen', e[1], '%g' % total, 'settle'] +
                     [x for x in e[2:] if x.startswith('rows=')])] + shot


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for block in iter(lambda: fh.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


class Plan:
    def __init__(self, path, port=None, quiet=False, din=None):
        self.path = os.path.abspath(path)
        self.here = os.path.dirname(self.path)
        self.settings, self.steps = parse(path)
        g = self.settings.get
        self.name = g('name') or os.path.splitext(os.path.basename(path))[0]
        self.unit = g('unit', 'rytm')
        self.emulator = g('emulator', 'no').lower() == 'yes'
        self.port = port or g('port') or PORTS.get(self.unit, 'Analog Rytm')
        self.din = (din or g('din')) if uses_din(self.steps) else None   # opened only for din steps
        self.quiet = quiet
        self.stdout = sys.stdout          # the verdict goes here, whatever the steps print
        stamp = time.strftime('%Y%m%d-%H%M%S')
        self.run_dir = os.path.join(os.path.normpath(self.rel(g('out', 'runs'))),
                                    '%s-%s-%s' % (re.sub(r'\W+', '-', self.name).strip('-'),
                                                  'emu' if self.emulator else self.unit, stamp))
        os.makedirs(self.run_dir, exist_ok=True)
        self.log = open(os.path.join(self.run_dir, 'log.txt'), 'a', encoding='utf-8')
        self.results = []                # (step, 'ok' | 'FAIL' | 'skip', detail)
        self.claimed = False

    def rel(self, p):
        return p if os.path.isabs(p) else os.path.join(self.here, p)

    def note(self, text):
        self.log.write(text + '\n')
        self.log.flush()

    def call(self, cmd, cwd=None, timeout=600):
        """Run a command, its output to the log; -> (returncode, last output line)."""
        self.note('$ ' + ' '.join(cmd))
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        self.note(r.stdout + r.stderr)
        lines = [l for l in (r.stdout + r.stderr).splitlines() if l.strip()]
        return r.returncode, (lines[-1].strip() if lines else '')

    # --- lock and flash ---------------------------------------------------------

    def lock(self, *args):
        cmd = self.settings.get('lock')
        if not cmd or self.emulator:
            return 0, 'no lock'
        return self.call(shlex.split(cmd, posix=False) + list(args), timeout=900)

    def flash(self):
        g = self.settings.get
        image = self.rel(g('flash'))
        digest = sha256(image)
        if not self.emulator:
            ok, why = self.emulator_gate(digest)
            if not ok:
                raise RuntimeError(why)
        cmd = [sys.executable, '-m', 'elkflash', 'upgrade', image, '--yes', '--no-confirm',
               '--wait', '--quiet', '--log', os.path.join(self.run_dir, 'elkflash.log')]
        if ',' in self.port:
            i, o = [p.strip() for p in self.port.split(',', 1)]
            cmd += ['--in-port', i, '--out-port', o]
        else:
            cmd += ['--port', self.port]
        lock = g('lock')
        if lock and not self.emulator:
            cmd = shlex.split(lock, posix=False) + ['run', self.unit, '--'] + cmd
        rc, last = self.call(cmd, cwd=g('elkflash'), timeout=900)
        if rc:
            raise RuntimeError('flash failed: ' + last)
        return digest, last

    def emulator_gate(self, digest):
        run = self.settings.get('emulator-run')
        if not run:
            return False, 'a hardware flash needs emulator-run = a passing emulator run of this plan'
        try:
            with open(os.path.join(self.rel(run), 'summary.json'), encoding='utf-8') as fh:
                s = json.load(fh)
        except OSError as e:
            return False, 'emulator-run has no summary.json: %s' % e
        if not (s.get('emulator') and s.get('pass')):
            return False, 'emulator-run %s did not pass in the emulator' % run
        if s.get('flash_sha256') != digest:
            return False, 'emulator-run flashed a different OS file (sha256 differs)'
        return True, ''

    # --- audio --------------------------------------------------------------------

    def audio(self, op, a):
        g = self.settings.get
        py = shlex.split(g('audio-python', sys.executable), posix=False)
        tools = g('audio-tools', '')
        wav = lambda n: os.path.join(self.run_dir, n + '.wav')
        if op == 'audio-tone':
            cmd = py + [os.path.join(tools, 'asio_tone.py'), g('audio-driver', ''), wav(a[1]), a[0], '0']
            cmd += a[2:3]
        elif op == 'audio-record':
            cmd = py + [os.path.join(tools, 'asio_rec.py'), 'rec', g('audio-driver', ''), a[0], wav(a[1])]
        elif op == 'audio-dropouts':
            cmd = py + [os.path.join(tools, 'audiocheck.py'), 'dropouts', wav(a[0]), '--quiet']
            cmd += ['--ch', a[1]] if len(a) > 1 else []
        elif op == 'audio-compare':
            ref = a[0] if a[0].endswith('.wav') else a[0] + '.wav'
            ref = ref if os.path.isabs(ref) else os.path.join(self.rel(g('refs', 'refs')), ref)
            cmd = py + [os.path.join(tools, 'audiocheck.py'), 'compare', ref, wav(a[1]), '--quiet',
                        '--png', os.path.join(self.run_dir, a[1] + '-compare.png')]
            cmd += ['--ch', a[2]] if len(a) > 2 else []
        else:
            raise ValueError('unknown step %r' % op)
        rc, last = self.call(cmd)
        if rc:
            raise AssertionError('%s %s: %s' % (op, ' '.join(a), last))

    # --- the run ------------------------------------------------------------------

    def runner(self):
        """The step runner's module: analogrun, or digirun for a Digitone plan."""
        if self.unit not in DIGI_UNITS:
            import analogrun
            analogrun.OUT = self.log
            return analogrun
        where = self.settings.get('digirun') or os.environ.get('DIGIRUN_PATH')
        if where:
            where = self.rel(where)
            sys.path.insert(0, os.path.dirname(where) if where.endswith('.py') else where)
        import digirun
        return digirun

    def run(self):
        t0 = time.monotonic()
        digest = None
        if self.din and self.unit in DIGI_UNITS:
            self.results.append(('din', 'FAIL', 'DIN steps run on the Rytm and A4 only so far'))
            return self.finish(t0, digest)
        try:
            mod = self.runner()
        except ImportError as e:
            self.results.append(('runner', 'FAIL', 'no digirun.py (digirun = its folder): %s' % e))
            return self.finish(t0, digest)
        # digirun prints what its steps report: into the log, as analogrun's OUT does
        with contextlib.redirect_stdout(self.log) if mod.__name__ == 'digirun' else contextlib.nullcontext():
            return self.run_with(mod, t0)

    def run_with(self, runner, t0):
        session = port = din = None
        digest = None
        try:
            rc, last = self.lock('claim', self.unit, '--for', 'test plan ' + self.name,
                                 '--wait', '--timeout', '10m')
            if rc:
                self.results.append(('claim', 'FAIL', last))
                return self.finish(t0, digest)
            self.claimed = bool(self.settings.get('lock')) and not self.emulator
            if self.settings.get('flash'):
                try:
                    digest, last = self.flash()
                    self.results.append(('flash', 'ok', last))
                except Exception as e:           # noqa: BLE001
                    self.results.append(('flash', 'FAIL', str(e)))
                    return self.finish(t0, digest)
            port = runner.open_port(self.port)
            try:
                din = runner.open_port(self.din) if self.din else None
            except BaseException:
                port.close()
                port = None
                raise
            session = runner.Session(port, self.run_dir, din=din) if din else runner.Session(port, self.run_dir)
            session.refs = self.rel(self.settings.get('refs', 'refs'))
            session.record_refs = self.emulator and self.settings.get('record-refs', 'no') == 'yes'
            failed = False
            for name, lines in self.steps:
                if failed or (self.emulator and name.endswith('(hardware)')):
                    self.results.append((name, 'skip', ''))
                    continue
                self.note('[%s]' % name)
                try:
                    for i, line in enumerate(lines):
                        self.note('  ' + line)
                        words = shlex.split(line, comments=True)
                        merged = None if session.record_refs else merge_wait(line, lines[i + 1:i + 3])
                        if merged:
                            # wait N then expect-screen: poll from now instead, so the
                            # check passes as soon as the screen is right (see merge_wait)
                            self.note('    (waiting for the screen instead)')
                            lines[i + 1:i + 1 + len(merged)] = merged
                        elif words and words[0].startswith('audio-'):
                            self.audio(words[0], words[1:])
                        elif words and words[0].lower() == 'listen' and self.emulator:
                            self.note('    (nobody listens to the emulator: skipped)')
                        else:
                            session.step(line)
                    self.results.append((name, 'ok', ''))
                except Exception as e:           # noqa: BLE001 - recorded as the step's failure
                    import traceback
                    traceback.print_exc(file=self.log)
                    self.results.append((name, 'FAIL', '%s: %s' % (type(e).__name__, e)))
                    failed = True
        finally:
            if session is not None:
                try:
                    session.close()
                except Exception as e:           # noqa: BLE001 - e.g. the unit restarted
                    self.note('closing the key session: %s' % e)
                finally:
                    port.close()
                    if din is not None:
                        din.close()
            if hasattr(runner, 'OUT'):
                runner.OUT = None
        return self.finish(t0, digest)

    def finish(self, t0, digest):
        ok = all(r[1] != 'FAIL' for r in self.results) and any(r[1] == 'ok' for r in self.results)
        first = next((r for r in self.results if r[1] == 'FAIL'), None)
        summary = {'plan': self.path, 'name': self.name, 'unit': self.unit, 'emulator': self.emulator,
                   'pass': ok, 'seconds': round(time.monotonic() - t0, 1), 'flash_sha256': digest,
                   'steps': [{'step': n, 'result': r, 'detail': d} for n, r, d in self.results]}
        with open(os.path.join(self.run_dir, 'summary.json'), 'w', encoding='utf-8') as fh:
            json.dump(summary, fh, indent=1)
        if self.claimed:
            note = 'test plan %s: %s' % (self.name, 'PASS' if ok else 'FAIL at %s' % first[0])
            if self.settings.get('note'):
                note += '; ' + self.settings['note']
            self.lock('release', self.unit, '--note', note)
        self.log.close()
        lines = ['%s on %s: %s (%d steps, %.0f s) - %s' % (
            self.name, 'the emulator' if self.emulator else self.unit, 'PASS' if ok else 'FAIL',
            len(self.results), summary['seconds'], self.run_dir)]
        if not self.quiet or not ok:
            lines += ['  %-4s %s' % (r, n) for n, r, _ in self.results]
        if first:
            lines.append('first failure, %s: %s' % (first[0], first[2]))
        print('\n'.join(lines), file=self.stdout)
        return 0 if ok else 1


def run_plan(path, port=None, quiet=False, din=None):
    if parse(path)[0].get('unit') in DIGI_UNITS and ('analogrun' in sys.modules or 'elkrpc' in sys.modules):
        # analogrun's elkrpc / midisock are loaded here: digirun needs its own, in a new process
        cmd = [sys.executable, os.path.abspath(__file__), path] + (['--port', port] if port else []) + \
            (['--din', din] if din else []) + (['--quiet'] if quiet else [])
        r = subprocess.run(cmd, capture_output=True, text=True)
        print(r.stdout, end='')
        sys.stderr.write(r.stderr)
        return r.returncode
    return Plan(path, port, quiet, din).run()


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='run a test plan (see the module docstring)')
    ap.add_argument('plan')
    ap.add_argument('--port')
    ap.add_argument('--din')
    ap.add_argument('--quiet', action='store_true')
    args = ap.parse_args()
    sys.exit(run_plan(args.plan, args.port, args.quiet, args.din))
