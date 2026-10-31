# Intent classifier — standalone

Routes a message to `ask`, `summary` or `draft` using Claude Haiku.

Self-contained: no Legal Lens backend, no database, no `uv`, no FastAPI. A virtualenv,
one dependency, and an API key.

## Setup

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

Put your key in `.env` on the `ANTHROPIC_API_KEY=` line. Get one at
[console.anthropic.com/settings/keys](https://console.anthropic.com/settings/keys) —
copy it from the **creation dialog**, which is the only place the secret is shown. It
starts `sk-ant-`; a value starting `apikey_` is the key's ID, not the key.

API credits are a separate purchase from a Claude.ai subscription. If your key is not
scoped to a workspace, also set `ANTHROPIC_WORKSPACE_ID=wrkspc_…`.

## Use

```bash
python3 classify.py "draft a memo on venue"
python3 classify.py --suite --verbose
python3 classify.py --show-prompt        # no key needed
```

## Output

```
intent      ask
confidence  0.95
reasoning   Asks to summarise one clause, not the whole document.
source      model
latency     1825ms
```

`source` says how the answer was reached: `model`, `low_confidence` (the model's choice
was below 0.35 and was floored to `ask`), `empty_input`, or `error`.

## How it decides

The guide is [`intent_prompt.md`](intent_prompt.md) — plain Markdown. Edit it and
re-run `--suite` to see what changed.

| Intent | Produces |
|---|---|
| `ask` | A grounded answer about what the documents say |
| `summary` | The fixed nine-field overview of a document |
| `draft` | New prose the attorney will edit and send |

Two design points worth knowing:

**`ask` is the fallback for everything that goes wrong.** Of the three, only `ask` can
conclude "the documents do not address this" and stop. `summary` asserts nine fields;
`draft` writes a document the attorney may sign. So an empty message, a low-confidence
answer, a malformed answer and a network failure all route to `ask` — the outcome that
fails safely. There is no fourth category: "Hello" routes to `ask` with confidence
around 0.15.

**The model cannot answer off-menu.** `intent` is an enum in the tool schema, so an
invalid value is impossible by construction rather than caught afterwards.

## Relationship to the main project

This folder is a **copy**, kept deliberately independent so it runs with nothing else
installed. `intent_prompt.md` is duplicated from
`backend/src/legal_lens/orchestrator/intent_prompt.md`.

The two can drift. If you tune the prompt here, copy it back:

```bash
cp intent_prompt.md ../backend/src/legal_lens/orchestrator/intent_prompt.md
```

The backend version is the one that will eventually run in production; this one is for
iterating quickly.
