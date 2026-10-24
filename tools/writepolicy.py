# SPDX-License-Identifier: GPL-2.0-or-later
"""An optional write policy: a Python file installed on one computer that the
runner asks before it changes anything on a unit, for example to keep
automated test sessions away from the person's own projects. Without one, the
runner writes wherever it is told and renames an item only when --name is
given.

The policy is the file named by ANALOGHEALTH_POLICY (empty: none), else
policy.py in the analoghealth config folder (%LOCALAPPDATA%\\analoghealth on
Windows, ~/.config/analoghealth elsewhere). It may define any of these; a hook
refuses by raising writepolicy.Refused:

    check_write(rpc, path, new_name)   before a write, clear or rename of path:
                                       -> the name the item carries afterwards,
                                       or None to leave it as written
    before_dumps(session, msgs)        before SysEx messages go to the unit
    after_save_project(session, path, name)   after a key-driven project save
    before_save_kit(session)           before a key-driven kit save
    describe_active(kit_name)          a short note for `analogrun active`

The last three may return a line for the runner to print. unitdata.py has the
data-store and kit-dump helpers a policy needs.
"""
import importlib.util
import os
import sys

_loaded = {}


class Refused(Exception):
    """A write the local policy does not allow."""


def config_dir():
    base = os.environ.get('LOCALAPPDATA') if sys.platform == 'win32' else None
    return os.path.join(base or os.path.join(os.path.expanduser('~'), '.config'), 'analoghealth')


def policy_path():
    """-> the policy file to use, or None."""
    if 'ANALOGHEALTH_POLICY' in os.environ:
        return os.environ['ANALOGHEALTH_POLICY'] or None
    return os.path.join(config_dir(), 'policy.py')


def policy():
    """The loaded policy module, or None when there is no policy file."""
    path = policy_path()
    if not path or not os.path.isfile(path):
        return None
    if path not in _loaded:
        spec = importlib.util.spec_from_file_location('analoghealth_policy', path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _loaded[path] = mod
    return _loaded[path]


def _hook(name):
    return getattr(policy(), name, None)


def installed():
    return policy() is not None


def check_write(rpc, path, new_name=None):
    hook = _hook('check_write')
    if hook:
        return hook(rpc, path, new_name)
    return new_name.upper() if new_name else None


def before_dumps(session, msgs):
    hook = _hook('before_dumps')
    return hook(session, msgs) if hook else None


def after_save_project(session, path, name):
    hook = _hook('after_save_project')
    return hook(session, path, name) if hook else None


def before_save_kit(session):
    hook = _hook('before_save_kit')
    return hook(session) if hook else None


def describe_active(kit_name):
    hook = _hook('describe_active')
    return hook(kit_name) if hook else None
