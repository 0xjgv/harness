"""behave environment hooks.

Every scenario builds a throwaway git repo in a tempdir and runs the harness inside
it, so no scenario may ever touch the developer's own repository.
"""

import os


def before_all(context):
    """Cut this process off from any enclosing git repository and global git config.

    A git hook exports GIT_DIR (and friends) into everything it runs, so when the
    acceptance gate runs from `pre-push`, `git init`/`git add -A` in a tempdir would
    otherwise operate on the *enclosing* worktree — staging its whole tree as deleted.
    Inherited env beats `cwd`, so stripping the variables is the only fix, and it has
    to cover the harness subprocess under test as well as the steps' own git calls.
    Neutralizing the global/system config on top keeps `git init`/`git commit`
    identical on every machine (gpg signing, commit templates, `core.hooksPath`).
    """
    for name in [key for key in os.environ if key.startswith("GIT_")]:
        del os.environ[name]
    os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
    os.environ["GIT_CONFIG_SYSTEM"] = os.devnull
