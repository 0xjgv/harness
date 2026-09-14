"""behave environment hooks.

Guard overrides an ambient shell may carry (`HARNESS_ALLOW_PROTECTED_PUSH=1` while
debugging a push, say) must not leak into the harnesses these scenarios spawn — a
scenario that needs one sets it explicitly.
"""

import os

GUARD_ENV_VARS = (
    "HARNESS_ALLOW_PROTECTED_PUSH",
    "HARNESS_ALLOW_ARCH_CONFIG",
    "HARNESS_PRE_PUSH_REFS",
)


def before_all(context):
    for name in GUARD_ENV_VARS:
        os.environ.pop(name, None)
