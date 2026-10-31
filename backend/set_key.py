#!/usr/bin/env python3
"""Write the Anthropic credentials into backend/.env without echoing the key.

Exists because `.env` is awkward to edit by hand here: it is a dotfile, and
`.gitignore` lists it, so editors that respect gitignore hide it from the file tree
entirely. This writes it for you.

Uses `getpass` rather than the shell's `read`, because `read -p` is bash-only and
fails under zsh with "no coprocess". The key is never printed and never reaches shell
history.

    python3 set_key.py                      # prompt for the key
    python3 set_key.py --workspace wrkspc_x # also set the workspace id
    python3 set_key.py --workspace-only wrkspc_x
"""

from __future__ import annotations

import getpass
import pathlib
import re
import sys

ENV = pathlib.Path(__file__).with_name(".env")
VAR = "ANTHROPIC_API_KEY"


def _set(var: str, value: str) -> None:
    """Replace `var` in .env, or append it if it is not there yet."""
    text = ENV.read_text()
    updated, count = re.subn(rf"^{var}=.*$", f"{var}={value}", text, count=1, flags=re.M)
    if count == 0:
        updated = text.rstrip("\n") + f"\n{var}={value}\n"
    ENV.write_text(updated)


def main() -> int:
    if not ENV.is_file():
        print(f"error: {ENV} not found", file=sys.stderr)
        return 2

    args = sys.argv[1:]
    workspace = None
    if "--workspace" in args:
        workspace = args[args.index("--workspace") + 1]
    if "--workspace-only" in args:
        # The workspace id is an identifier, not a secret, so it can be passed on the
        # command line without the care the key needs.
        _set("ANTHROPIC_WORKSPACE_ID", args[args.index("--workspace-only") + 1])
        print("written to .env: ANTHROPIC_WORKSPACE_ID set")
        return 0

    key = getpass.getpass("paste the key (it will not be shown), then Enter: ").strip()
    if not key:
        print("nothing entered; .env unchanged")
        return 1

    # Catch the most common copy-paste mistake: the console's key *identifier*
    # instead of the secret. Only the secret can authenticate, and it is shown just
    # once, at creation.
    if not key.startswith("sk-ant-"):
        print(
            f"\nthat does not look like an Anthropic secret key.\n"
            f"  what you pasted starts: {key[:11]}…\n"
            f"  a secret key starts:    sk-ant-api03-…\n\n"
            f"If it begins with 'apikey_' that is the key's ID from the console list, "
            f"not the key. The secret is only displayed when the key is created, so "
            f"create a new one and copy the value from that dialog.\n",
            file=sys.stderr,
        )
        return 3

    _set(VAR, key)
    print(f"written to {ENV.name}: {VAR} set ({len(key)} chars)")
    if workspace:
        _set("ANTHROPIC_WORKSPACE_ID", workspace)
        print(f"written to {ENV.name}: ANTHROPIC_WORKSPACE_ID set")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
