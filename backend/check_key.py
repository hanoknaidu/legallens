#!/usr/bin/env python3
"""Tell me whether my API key is set up and usable.

    python3 check_key.py

Checks three things in order, because they fail differently and the fixes differ:
  1. is a key present in backend/.env or the environment?
  2. does it authenticate?
  3. does the account have credit to actually run a call?
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, "src")


def main() -> int:
    from legal_lens.config import Settings

    env = pathlib.Path(__file__).with_name(".env")
    settings = Settings(_env_file=env if env.is_file() else None)
    key = settings.anthropic_api_key

    if not key:
        print("1. key present ....... NO")
        print(f"\n   Put it on line 7 of {env}:")
        print("     ANTHROPIC_API_KEY=sk-ant-...")
        return 1
    print(f"1. key present ....... yes ({len(key)} chars, {key[:11]}…)")

    if not key.startswith("sk-ant-"):
        print("2. authenticates ..... NO — that is not an Anthropic secret key")
        print("\n   A secret starts 'sk-ant-'. A value starting 'apikey_' is the key's")
        print("   ID from the console list, not the key. The secret is shown only once,")
        print("   in the dialog when the key is created.")
        return 1

    from anthropic import Anthropic, APIStatusError

    client = Anthropic(api_key=key, default_headers=settings.anthropic_headers or None)
    try:
        # One token: the cheapest call that still proves auth and billing.
        client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=1,
            messages=[{"role": "user", "content": "hi"}],
        )
    except APIStatusError as exc:
        detail = ""
        try:
            detail = exc.response.json().get("error", {}).get("message", "")
        except Exception:
            detail = str(exc)

        if exc.status_code == 401:
            print("2. authenticates ..... NO (401 invalid x-api-key)")
            print("\n   The key was rejected. Create a new one at")
            print("   https://console.anthropic.com/settings/keys and copy the value")
            print("   from the creation dialog.")
            return 1

        print("2. authenticates ..... yes")
        if "workspace" in detail.lower():
            print("3. usable ............ NO — the key needs a workspace")
            print(f"\n   {detail}\n")
            print("   Two ways to fix it, either is fine:")
            print("     a) Create a key inside a workspace:")
            print("        console.anthropic.com/settings/keys -> Create Key -> pick a workspace")
            print("     b) Keep this key and name the workspace in backend/.env:")
            print("        ANTHROPIC_WORKSPACE_ID=wrkspc_...")
            print("        (find it under Settings -> Workspaces; it is in the URL)")
            return 1

        if "credit balance" in detail.lower():
            print("3. has credit ........ NO")
            print(f"\n   {detail}")
            print("\n   API credits are separate from a Claude.ai Pro/Max subscription.")
            print("   Add them at https://console.anthropic.com/settings/billing")
            return 1

        print(f"3. has credit ........ unclear — API said: {detail[:90]}")
        return 1
    except Exception as exc:
        print(f"2. authenticates ..... could not tell: {type(exc).__name__}: {exc}")
        return 1

    print("2. authenticates ..... yes")
    print("3. has credit ........ yes")
    print("\nReady. The key is usable for generation, OCR and the orchestrator.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
