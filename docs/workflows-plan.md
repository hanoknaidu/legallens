# Ask · Summary · Draft — Workflow Plan

Written 2026-10-12. **Implemented 2026-10-16** — see §9.2 for what the build changed
about this plan, and `backend/src/legal_lens/orchestrator/` for the code.
**Serving requests since 2026-10-19** — §9.3 covers the integration and what it
changed about the seam it landed behind. **§9.4 is a gap pass over the integrated
service**, which reversed §1's decision on FR-13 grounds and found that §9.3's
prescribed caching fix would have cached nothing. **§9.5 re-measures reranking on a
corpus where candidates < corpus — reversing §9.2's conclusion — and cuts the cache
bill.**

**Decided:** take the reranker regardless of CPU cost (latency deprioritised); eval
corpus generated rather than taken from real contracts.
**Decided 2026-10-16 (§1):** **always retrieve** — no whole-document threshold on
`ask`. The absence-question risk this creates is handled by a coverage flag rather
than by a threshold; §9.2 explains how.

Scope: the AI and retrieval layer. API wiring is out of scope by your instruction —
and as it happens almost none is needed, because these workflows land behind the
`GenerationProvider` seam that `api/chat.py` already talks to.

---

## 0. What already exists, so the delta is clear

Phases 2–3 of [integration-TODO.md](integration-TODO.md) built a working pipeline.
This plan **replaces three pieces of it** and leaves the rest alone.

| Piece | Today | After this plan |
|---|---|---|
| Embeddings | `HashingEmbedder` — a hashed bag of words with **no notion of meaning** | A real sentence-embedding model (§3) |
| Vector store | `MemoryVectorStore` — exhaustive cosine, process-local, rebuilt at boot | Qdrant, dense + sparse on one point (§4) |
| Fusion | Reciprocal-rank fusion in `retrieval/select.py`, application-layer | Qdrant's **server-side** RRF |
| Reranking | None — `rerank_min_score` is configured and unused | Cross-encoder reranker (§3) |
| Query rewriting | None | Haiku pre-retrieval step (§5) |
| Generation | One prompt per intent, `echo` + Anthropic providers | Three distinct workflows (§5–§7) |
| Chunking, ingestion, citations, audit, SSE | Working and tested | **Unchanged** |

The citation mechanism is worth calling out as unchanged on purpose: pages go to the
API as a custom-content document's content blocks, and `content_block_location`
resolves back to a page by position. That is what makes a cited page correct by
construction rather than by trusting model prose, and nothing in this plan disturbs it.

---

## 1. The one decision I need from you

**Does hybrid retrieval replace the whole-document path, or sit above it?**

BRD §4.2 selects per request: below `whole_document_token_budget` (600K) the entire
corpus in scope goes into context and retrieval is skipped. That is not an
optimisation — it is what makes **absence questions** answerable. "The contract is
silent on insurance" is only provable if the whole contract was read. The BRD's own
risk table lists *"Absence questions fail under retrieval"* with the whole-document
path as the mitigation, and FR-13 depends on it.

A hybrid-search pipeline that always runs would regress that. Three options:

| | Behaviour | Cost |
|---|---|---|
| **A — Keep the threshold** *(my recommendation)* | Whole-document below 600K; hybrid + rerank above it | Hybrid search is dormant on a typical 40-page MSA, so you won't see it working day to day |
| **B — Always retrieve** | Hybrid + rerank on every question | Absence questions become unreliable; FR-13 and NFR-1 both weaken |
| **C — Always retrieve, but widen for absence** | Route on question *type*: absence/completeness questions take the whole-document path, everything else retrieves | Needs a reliable question classifier — a fourth Haiku call and a new failure mode |

I recommend **A**, with the threshold dropped to something like 120K tokens so hybrid
retrieval actually exercises on realistic corpora rather than only on 500-page ones.
That gives you the pipeline you want, running often, without trading away absence
correctness. **Tell me if you'd rather have B or C.**

---

## 2. Model routing

Yours, with the API constraints that each one imposes. These are not stylistic — the
wrong parameter is a 400.

| Step | Model | ID | Hard constraints |
|---|---|---|---|
| Query rewrite | Haiku 4.5 | `claude-haiku-4-5` | **`output_config.effort` errors on this model** — do not send it. Thinking is `budget_tokens` or off; off is right for a mechanical rewrite. |
| **Ask** | Sonnet 5.5 | `claude-sonnet-5-5` | `thinking: {type:"disabled"}` returns **400**. Use adaptive thinking at `low`–`medium` effort, or `{type:"between_tools"}`. Effort is recalibrated from Sonnet 5 — re-tune, don't port numbers. |
| **Summary** | Sonnet 5.5 or Opus 5.5 | both | Routing rule below |
| **Draft** | Opus 5.5 | `claude-opus-5-5` | Thinking cannot be disabled at any effort. Default effort is **`medium`**, so set `high` explicitly or you silently get less than you asked for. `budget_tokens` → 400. |

Applying to all three generation steps:

- **Citations are incompatible with `output_config.format`** (structured outputs) — a
  400. So the nine summary fields cannot be extracted with a JSON schema in the same
  call that produces citations. This is the BRD's own §4.1 risk, and §6 resolves it.
- Forced `tool_choice: any`/`tool` → 400 on Opus 5.5 and Sonnet 5.5.
- No assistant prefill on any current model.
- Stream everything: these calls carry whole contracts and high `max_tokens`.

**Config change:** `generation_model` becomes four fields — `ask_model`,
`summary_model`, `draft_model`, `rewrite_model` — plus `*_effort` per workflow, since
the right effort differs by task and by model.

### Summary routing rule (concrete, so it isn't left to taste)

Route on **how much reconciliation the answer needs**, not on page count alone:

- **Sonnet 5.5** — a single document in scope.
- **Opus 5.5** — two or more documents in scope, or any document whose title or kind
  marks it an amendment.

The reason: summarising one contract is extraction, which Sonnet does well. The hard
case is *"does Amendment No. 2 change the term in the MSA?"* — reconciling documents
that contradict each other — and that is where the stronger model earns its price.
Expose it as `summary_opus_above_documents: int = 2` so you can tune it without a
code change.

---

## 3. Embedding and reranking models

Both run **locally**, which is not only your preference but a requirement hiding in
the BRD: §8.2 treats an embedding vendor as *a second processor of privileged
attorney–client text* needing its own DPA before it is switched on. A local
open-weight model sidesteps that entirely. Your instinct and the compliance
constraint point the same way.

*(Aside: OpenAI does have embedding models — `text-embedding-3-small`/`-large`. It
doesn't change the recommendation, since local-only is the right call here for the
privilege reason above.)*

### Embedding — recommend `Qwen3-Embedding-0.6B`

| | Model | Dim | Context | Licence | Why |
|---|---|---|---|---|---|
| **Primary** | `Qwen/Qwen3-Embedding-0.6B` | **1024** | 32K | Apache 2.0 | Best current open-weight family on MTEB; the 0.6B runs comfortably on your M-series Mac. **1024 dims matches `embedding_dimensions` already in your config** — no migration. |
| Alternative | `BAAI/bge-m3` | 1024 | 8K | MIT | Longer-proven, very well supported, strong on long passages. Pick this if you hit friction with Qwen tooling. |
| If you add a GPU | `Qwen/Qwen3-Embedding-8B` | 4096 | 32K | Apache 2.0 | ~75% MTEB average, 5th on MTEB English v2. Needs real VRAM; not a laptop model. |

The 32K context matters more here than it looks: chunks are ~2800 chars, but a long
clause plus its carried-forward defined terms can run well past a 512-token encoder,
and truncation at the encoder is a silent recall failure.

### Reranking — recommend `BAAI/bge-reranker-v2-m3`

| | Model | Size | Licence | Why |
|---|---|---|---|---|
| **Primary** | `BAAI/bge-reranker-v2-m3` | ~0.6B | Apache 2.0 | Cross-encoder, ~2.5GB, BEIR ≈ 67.6. **At this size it beats `Qwen3-Reranker-0.6B`**, which underperforms it on BEIR — so the obvious "match the Qwen family" choice is the wrong one here. |
| Alternative | `Qwen/Qwen3-Reranker-0.6B` | ~0.6B | Apache 2.0 | Better instruction-following; worth testing if you want to steer ranking with a prompt. |
| If you add a GPU | `Qwen/Qwen3-Reranker-8B` | 8B | Apache 2.0 | BEIR ≈ 69.0, the strongest open option. |

**Mixing families is deliberate**, and it is fine: the embedder and the reranker never
see each other's vectors. The reranker reads `(query, chunk_text)` as text.

### How they run

In-process via `sentence-transformers` (already an optional extra in your
`pyproject.toml`), each call wrapped in `asyncio.to_thread` so a 300ms encode does not
block the event loop and stall every other request including the SSE streams.

- **Verified:** `torch 2.14.1` and `sentence-transformers` resolve on your Python
  3.14.7 venv. This was the thing most likely to block the whole plan; it doesn't.
- Warm both models at lifespan, not on first request — otherwise the first question
  after every restart pays a multi-second model load, and `/health` should report
  them as a check so "the first answer is slow" is diagnosable.
- First run downloads ~1.2GB of weights. Pin revisions, and make `/health` degrade
  rather than crash if the cache is cold and the machine is offline.

---

## 4. Vector store — Qdrant, embedded

Your config already declares `vector_store: Literal["qdrant", "memory"]` and
`qdrant_url`, so Qdrant is the path of least resistance. Chroma would be a new
dependency for no gain.

**Verified empirically** (throwaway env, nothing installed into the project): Qdrant's
**embedded local mode** — `QdrantClient(path="./var/qdrant")`, no server, no Docker —
supports all of:

- a collection with a named dense vector *and* a named sparse vector,
- `SparseVectorParams(modifier=Modifier.IDF)`, which computes the IDF half of BM25
  server-side,
- both vectors upserted on one point, so a chunk's dense and sparse representations
  update atomically,
- `prefetch` + `FusionQuery(fusion=Fusion.RRF)` — **fusion happens in the store**, not
  in application code.

So the `retrieval/select.py` hand-rolled RRF goes away, and dev needs no container.
`qdrant_url` stays as the production path; local mode is selected by leaving it unset.

BM25 sparse vectors need a tokenizer that agrees with itself across index and query.
Recommend `fastembed`'s `Bm25` (Qdrant's own, built for exactly this pairing) over
reusing `lib_text.tokens` — one dependency, and correct stemming and stopwords for
free.

---

## 5. Ask workflow

```
question + history
   │
   ├─ 0. history empty?  ──yes──► skip rewrite entirely
   │
   ├─ 1. PRE-RETRIEVAL   Haiku: rewrite, or decline to
   │
   ├─ 2. RETRIEVAL       Qdrant hybrid: dense top-25 ∥ BM25 top-25 → server RRF
   │
   ├─ 3. RERANK          bge-reranker-v2-m3 over 25 pairs → top 8
   │
   ├─ 4. AUGMENT         pages → custom-content document blocks, citations on
   │
   └─ 5. GENERATE        Sonnet 5.5, streamed, native citations
```

### Step 0 — the empty-history guardrail is code, not prompt

Your first guardrail ("if chat history is empty, don't rephrase") becomes an early
return, not a prompt instruction. A prompt can be disobeyed; a branch cannot. It also
saves a call and its latency on every first question in a conversation, which is a
large fraction of all questions.

### Step 1 — the rewrite, and its output contract

The second guardrail ("if history and question are independent, don't rewrite") needs
judgment, so it does live in the prompt. But the model must be able to **say** it
declined rather than leave us to infer it by diffing strings — a rewrite that happens
to equal the original is indistinguishable from a refusal otherwise.

So the contract is a sentinel:

```
NO_REWRITE
```
or
```
REWRITE: <the self-contained question>
```

Draft system prompt (the nuances you asked for, made enforceable):

> You rewrite a follow-up question so it stands alone, using the conversation that
> came before it. You do not answer it.
>
> Reply `NO_REWRITE` — exactly that, nothing else — when any of these holds:
> - the question is already self-contained,
> - the question is about a different subject from the conversation so far,
> - the history does not supply any term the question is missing.
>
> Otherwise reply `REWRITE: ` followed by the rewritten question, on one line.
>
> When you do rewrite:
> - Resolve only pronouns and references that the history actually resolves
>   ("it", "that clause", "the second one", "what about termination").
> - Change nothing else. Keep the user's own terms, and keep quoted language exactly.
> - Add no facts, no legal terms the user did not use, and no assumptions about what
>   they meant.
> - Never narrow or broaden what was asked. "What about indemnification?" after a
>   question about the MSA becomes "What does the MSA say about indemnification?" —
>   not "What are the indemnification caps in the MSA?"
> - If resolving a reference requires guessing between two candidates, reply
>   `NO_REWRITE`. A wrong rewrite retrieves the wrong clauses and the user cannot
>   see that it happened.

That last rule is the important one. A bad rewrite is worse than no rewrite, because
it fails *invisibly*: the user sees a confident answer to a question they didn't ask.

**Logging:** record the original, the verdict, and the rewrite on the audit row. When
an answer is wrong, the first question is always whether retrieval was even looking
for the right thing.

### Steps 2–3 — retrieval and reranking

- Dense: embed the rewritten question, `limit=25`.
- Sparse: BM25 over the same question, `limit=25`.
- Qdrant fuses with RRF server-side.
- Rerank the fused candidates with the cross-encoder, keep **8** (inside your 5–10).
- Expand surviving chunks to the pages they span — a cited span may sit on either page
  of a chunk that straddles a boundary.

Config: `retrieval_candidates: 50 → 25`, `retrieval_top_n: 8` (unchanged),
`rerank_min_score` finally used as the cross-encoder's floor.

### Step 5 — generation

Unchanged from what's built: custom-content documents, `citations: {enabled: true}`,
streamed, abstention on `NOT_IN_DOCUMENTS`. Only the model and effort change.

---

## 6. Summary workflow

The BRD flags this one as a genuine conflict (§4.1): **structured output and citations
cannot coexist in one call.** It proposes two-pass generation. What's built already
sidesteps it in one pass — ask for `## Heading` prose with citations enabled, then
parse the headings — and that works. I'd keep it, with one addition:

- **Retrieval:** always the whole document for summary, regardless of the §1 decision.
  A summary of retrieved fragments is not a summary; and the nine fields include
  *Open items*, which is an absence question by nature.
- **Generation:** Sonnet or Opus per §2's rule, effort `high`.
- **Parsing:** as today — a missing heading and an explicit `NOT PRESENT` both become
  `value: null`, so FR-13 holds either way.
- **New:** a second pass only when the first returns fewer than nine headings, asking
  the model to fill the ones it skipped. One retry, not a loop.

---

## 7. Draft workflow

- **Retrieval:** hybrid, but with a wider keep — **12** chunks rather than 8. A memo
  cites more ground than an answer, and a missing clause shows up as an
  `[ATTORNEY INPUT REQUIRED]` the attorney then has to research by hand.
- **Generation:** Opus 5.5, effort `high`, `draft_max_output_tokens: 16000` (already
  configured).
- **The marker is the deliverable.** FR-16/FR-17 turn on
  `[ATTORNEY INPUT REQUIRED]` appearing wherever the documents don't supply a fact.
  The prompt must make marking *cheaper than writing around it*, because a model's
  instinct is to produce smooth prose, and smooth prose over a gap is the single most
  dangerous output this product can make. Current export tests already pin the
  marker's survival through .docx and .md.
- **No conclusions.** BRD §8.3: this tool describes what documents say and never
  advises. The draft prompt states that, and the FR-17 notice ships inside the file.

---

## 8. Token economics — the seams, and nothing more

You've deprioritised this, so: cost rows already exist per model call
(`usage_costs`, `purpose`). The only addition is that the rewrite call gets its own
row with `purpose="rewrite"`, so when you do come back to optimise, the question
"what is the rewrite step costing us?" is already answerable from data rather than
needing new instrumentation. Same for the NFR-2 latency budget — `generation_ms` and
`request_ms` are already observed; nothing enforces them, and per your instruction
nothing will.

One thing I'd not defer: **prompt caching**. The system prompts and the document
blocks are the large, stable part of every request, and caching them is a free win
rather than a quality trade. Worth a `cache_control` breakpoint after the documents
from day one — it costs one line and is awkward to retrofit once the prompt
assembly has grown.

---

## 9. Order of work

1. **Spike (half a day).** Load both models, embed and rerank a real 40-page MSA,
   measure wall-clock on your Mac. If the reranker is unusably slow on CPU/MPS, better
   to know before anything is built on it.
2. **Embeddings + Qdrant.** Replace `HashingEmbedder` and `MemoryVectorStore` behind
   the existing interfaces; re-index on upload; rebuild at boot as today. The existing
   retrieval tests should pass unchanged — that's the point of the seams.
3. **Reranker.** Insert between fusion and page expansion.
4. **Ask workflow.** Rewrite step, then wire the pipeline, then switch to Sonnet.
5. **Summary**, then **Draft**.
6. **Evaluate.** A fixed set of ~20 questions over the demo corpus with known correct
   pages, run against: hashing+no-rerank (today), real embeddings+no-rerank, and
   full hybrid+rerank. Without this, "the retrieval is better now" is an assertion.

Steps 2 and 3 are where the quality comes from. Step 4 is where the behaviour you
asked for comes from.

---

## 9.1 Spike results — run 2026-10-12

The eval set is built (`tests/eval/`: 6 documents, 25 pages, 22 questions) and
validated. Three findings, one of which changes the plan's priorities.

### The first version of this eval was worthless, and that is the headline

It reported **page recall@8 = 0.97 for the hashing embedder** — the one with no notion
of meaning. Not because it is good: the corpus was 14 pages and the pipeline keeps 8,
so 57% of everything reached the prompt regardless of ranking. Membership at a
generous k cannot discriminate between retrieval strategies.

Fixed by adding **near-miss distractor documents** (a second MSA with different
figures, a software licence, a second NDA — same clause headings, different substance)
and by scoring **recall@1 / @3 / MRR** rather than membership at k=8. Adding the
distractors also silently invalidated four `absence` questions, because the licence
introduced force-majeure, assignment and data-residency clauses the real corpus
deliberately lacked; `tests/eval/validate.py` now catches that class of mistake, and
the absence questions were re-pointed at things genuinely absent from the pooled set.

An eval that flatters whatever you build is worse than no eval.

### Measured

18 scored questions, 25 pages, 25 candidates per arm → RRF → keep 8.

| Configuration | recall@1 | recall@3 | recall@8 | MRR | wrong page ranked first |
|---|---|---|---|---|---|
| Hashing + BM25 *(today)* | 0.47 | 0.69 | 0.83 | 0.67 | 8/18 |
| MiniLM + BM25 | **0.61** | **0.83** | **1.00** | **0.83** | **5/18** |

Per kind, recall@1:

| Kind | Hashing | MiniLM |
|---|---|---|
| lookup | 0.80 | **1.00** |
| paraphrase | 0.42 | 0.42 |
| collision | 0.25 | **0.50** |
| cross_doc | 0.33 | **0.50** |

### What this changes: the reranker matters more than the embedder

**MiniLM already reaches recall@8 = 1.00.** Every page that answers a question is
inside the top 8 — the right pages are *being retrieved* and *ranked badly*.
recall@1 is 0.61 and five questions still put a wrong page first.

That is a reordering problem, and a cross-encoder is exactly a reordering step. So
§9's claim that "steps 2 and 3 are where the quality comes from" is half right: on
this corpus, **step 3 (reranking) is where the remaining headroom is**, and the
embedder upgrade mostly buys the `lookup` and `collision` gains already visible above.

**`paraphrase` is flat at 0.42 for both.** A small general-purpose embedder does not
close the gap between "is there a ceiling on how much either side can pay" and "shall
not exceed". This is the specific thing a stronger embedder and the reranker have to
fix, and it is now measurable rather than assumed.

### What could not be measured

**`Qwen3-Embedding-0.6B` and `bge-reranker-v2-m3` would not download.** Hugging Face
throttles unauthenticated requests from this machine hard — the Qwen weights crawled
from 32MB to 64MB over ten minutes and then failed outright with
`OSError: Can't load the model`. The numbers above therefore use
`all-MiniLM-L6-v2` (384-dim, 22M params, already cached), which is a **far weaker
model than either recommendation** — so treat the MiniLM column as a *lower bound* on
what a real embedder gives, not as the recommended configuration's result.

To finish the spike: set `HF_TOKEN` in the environment and re-run
`uv run python tests/eval/run_spike.py` with no `SPIKE_*` overrides. That fills in
rows 2 and 3 of the table with the models actually being recommended.

Timings, for what they are worth given latency is deprioritised: MiniLM loads in 35s
cold, encodes a page in ~50ms on MPS, and adds ~5ms per question at query time.

---

## 9.2 Build results — 2026-10-16

All three workflows are implemented, in `backend/src/legal_lens/orchestrator/`, and
run from `cli.py` against the eval corpus with nothing else booted. Four things the
build changed about the plan above.

### §1 was decided as "always retrieve", and the absence risk is handled without a threshold

`ask` now runs hybrid retrieval on every question. The FR-13 problem that creates —
"the contract is silent on arbitration" is not provable from eight pages of two
hundred — is solved by a flag rather than by routing:

`Retrieved.coverage` is true when the selected pages are *every* page in scope, and
the system prompt is built from it. Under full coverage the model may state an
absence; under partial coverage it is told it is seeing a selection, forbidden from
asserting silence, and its abstention is worded *"the excerpts retrieved do not
address this"* rather than *"the documents do not address this"*. No classifier, no
threshold, no extra model call — the unsafe claim is simply unavailable in the case
where it would be unsafe.

Verified both ways against the real model:

| Scope | coverage | Answer |
|---|---|---|
| Whole corpus (8 of 25 pages) | `False` | *"The excerpts retrieved for this question do not address it. This is not a finding that the documents are silent."* |
| `--doc nda` (2 of 2 pages) | `True` | *"No. The NDA contains no non-compete clause."* |

### Reranking measured *worse* than no reranking, and this contradicts §9.1

§9.1 concluded that "step 3 (reranking) is where the remaining headroom is". On this
corpus, with every cross-encoder that will download to this machine, the opposite is
true:

| Configuration | recall@1 | recall@3 | recall@8 | MRR | wrong page first |
|---|---|---|---|---|---|
| Hashing + BM25 *(the old pipeline)* | 0.47 | 0.69 | 0.83 | 0.67 | 8/18 |
| **MiniLM + BM25, no rerank** | **0.61** | **0.83** | **1.00** | **0.83** | **5/18** |
| + `ms-marco-MiniLM-L-6-v2` | 0.56 | 0.83 | 0.97 | 0.80 | 6/18 |
| + `BAAI/bge-reranker-base` | 0.33 | 0.67 | 0.97 | 0.60 | 11/18 |

Not a truncation artefact — no page exceeds 277 tokens against a 512-token limit.
`ms-marco` is a web-passage ranker that scores every legal page at about −10, i.e.
uniformly irrelevant, so its ordering within that band is close to noise. On
`lookup`, the easiest group, it drops recall@1 from 1.00 to 0.60.

**The step is kept and left on**, because the measurement's regime is not the
production one: the eval corpus is 25 pages and each arm fetches 25 candidates, so
fusion returns the entire corpus and the reranker is globally re-sorting rather than
refining a shortlist. `ORCH_RERANK_ENABLED=0` turns it off in one variable, and the
full table is recorded on `OrchestratorConfig.rerank_enabled` so nobody re-enables it
without seeing it. What would settle the question is `bge-reranker-v2-m3` (needs
`HF_TOKEN`) on a corpus where candidates < corpus.

### A score floor against a logit-output reranker is a retrieval outage

`rerank_min_score` defaults to **no floor**. The configured `0.02` would have been
catastrophic: `ms-marco-MiniLM` returns raw logits in the −11..0 range even for the
passage that answers the question, so any floor at or above zero discards every
candidate. The symptom is not an error — it is an assistant that abstains on
questions the documents plainly answer. A floor is only meaningful once the scale of
the reranker actually in use has been looked at.

### The rewrite guardrails needed two operational rules, found by measuring

The first run of `--rewrite-suite` scored 13/15, and all three failures were the same
one: the model resolved an ambiguous reference by picking the likelier candidate, and
inherited "cap" from a previous answer into an open question. Two prompt additions
took it to **15/15**:

- **The counting test.** Count the candidates the reference could point to; two or
  more means decline. Stated as a procedure with two worked examples, not as advice.
- **Carry over the subject, never the predicate.** Take the document or the clause
  from the history; never the verb. *"Does the MSA cap liability?"* → *"What about
  indemnification?"* must not become *"Does the MSA cap indemnification?"*.

Both are the invisible failure mode: retrieval fetches the wrong passages and the
attorney sees a confident answer to a question they did not ask.

### Still open

- `Qwen3-Embedding-0.6B` remains undownloadable without an `HF_TOKEN`. A config
  value; no code change needed. (`bge-reranker-v2-m3` **is** present and has since
  run end to end in the service — see §9.3.)
- Prompt caching is working (`cache_read` is non-zero on repeat calls) but only the
  system prompt hits reliably on `ask`, because retrieved pages vary per question.
  Summary and draft cache their documents properly.
- ~~Integration into `POST /chat/message`~~ — **done 2026-10-19, see §9.3.**

---

## 9.3 Integration — 2026-10-19

The workflows now serve requests. `LEGAL_LENS_LLM_PROVIDER=orchestrator` selects them;
`anthropic` and `echo` are untouched, because `echo` is what the test suite and the
offline path run on and the `workflows` extra is too heavy to make a default.

Five things the integration needed, four of them not in the plan above.

### The generation seam had to learn that a provider can retrieve

`GenerationProvider` was defined around pages the runner had already chosen, which no
workflow here can accept: the prompt is conditioned on `Retrieved.coverage`, so what
is sent and what may be concluded are one decision. The protocol gained
`owns_retrieval`; a provider that sets it is handed `scoped_document_ids` and the
conversation history instead of pages, and reports back a `RetrievalReport` that the
audit row is built from. The alternative — running `select_pages` and discarding it —
would have embedded the whole corpus on every question to produce a list nothing read.

### The `citations.py` duplication is collapsed

`ai/anthropic_provider.py` carried its own `_documents()` and `_resolve()`, a second
copy of `build_documents()` and `resolve()`. It now uses the shared ones and is 67
lines shorter. One owner for the block-index-to-page mapping, which is the part that
cites the wrong page confidently when two copies drift.

### Owner scoping is structural, not remembered

`PassageIndex` carries no owner in its payload and filters by document id, because it
was built against a single-tenant eval corpus. Rather than widen it,
`OrchestratedRetrieval.search()` takes a **required, non-empty** document scope and
has no "search everything" mode; the one caller resolves that set from
`documents.list(owner_id)`. Phase 2 should also put `owner_id` in the payload — this
is currently one correct call site, not defence in depth.

### Deletion had to reach Qdrant, which nothing was doing

`DELETE /documents/{id}` cascaded inside SQLite and purged the object store, and left
both vector indexes alone. That was survivable for the in-memory store, which is
rebuilt from ready documents at boot. Qdrant persists, so a deleted document would
have kept answering questions indefinitely. It now gets a `vector_index` deletion job
alongside the object-store one, so an incomplete purge is visible rather than assumed
(NFR-8).

### A turn is one audit row and several cost rows

`ask` on a follow-up spends on Haiku and then on Sonnet. `Finished.calls` carries
every call, the audit row totals them and names the model that wrote the answer, and
`usage_costs` gets one row per call keyed by `purpose`. The intent classifier is
costed the same way with a null `audit_id`, because it runs in `POST /chat/message`
before the turn's audit row exists.

### Two smaller findings

- **Summary and draft stream no text.** The mock fixtures persist `content: ""` with
  the payload in `summary`/`draft`, and `echo.py` already stated the rule — a
  half-rendered summary table reads as broken. It also sidesteps the summary retry
  pass, which re-emits the headings it was asked to fill. `AnthropicProvider` streams
  the prose anyway and is now the outlier; left alone rather than changed under cover
  of this work.
- **The rewrite step would have blocked the event loop.** `QueryRewriter.rewrite()` is
  a synchronous LangChain call made from inside an async `run()` — fine for the CLI,
  not under uvicorn. The provider performs it in a worker thread and injects the
  result, which also means a first turn (no history, so nothing to resolve) never
  constructs a LangChain client at all.

### Verified against a real corpus, and one number in this plan is wrong

Retrieval was run end to end through the real service — real upload, real chunker,
embedded Qdrant, `bge-m3` and `bge-reranker-v2-m3` — on a generated 44-page MSA with
five distinctive clauses buried in boilerplate. All five were found in the top pages,
`coverage` was `False` on every partial read and `True` for `whole()`, and a deleted
document left the index.

It also showed that **`ask_top_n` counts chunks, not pages**. This plan and §9.2 both
talk about "8 pages to the model"; the measurement was on the eval corpus, where a
passage *is* a page, so the two were the same number and nothing revealed the
difference. On a real document the chunker produced 22 chunks over 44 pages, and the
8 surviving chunks expanded to **21 pages**:

| | eval corpus | real 44-page MSA |
|---|---|---|
| chunks for the corpus | 25 (one per page) | 22 (~2 pages each) |
| `ask_top_n` | 8 | 8 |
| pages actually sent | 8 | **21** |

Which means the `ask` prompt is roughly 2.6× the size this plan assumed, and the
recall@3 argument in §9.2 — "for a workflow that sends 8 pages to the model, recall@3
is the metric that tracks answer quality" — is reasoning about a prompt shape that
does not occur in production. Nothing is broken by it: the pages are the right pages,
and page expansion is deliberate because a chunk straddling a page boundary has no
single page to cite. But `ORCH_ASK_TOP_N` is not the lever it reads as, and the
re-measurement §9.2 asks for should count pages rather than passages.

### Run against the real models — 2026-10-19

All three workflows were run through `POST /chat/message` → `GET /chat/stream` with
`llm_provider=orchestrator` and `intent_router=model`, over an 8-page MSA. Everything
held:

| | result |
|---|---|
| Intent routing (Haiku) | 5/5 correct — three `ask`, one `summary`, one `draft` |
| Citations | every `citedText` present **verbatim** on the page it named (FR-7/FR-8) |
| Query rewrite | *"And how long is it capped for?"* resolved, and the answer correctly separated the twelve-month **lookback** from a cap **duration** — the §9.2 "subject, not predicate" rule doing its job |
| Absence | *"a non-compete clause?"* abstained under full coverage, worded as a finding about the documents |
| Summary | 9/9 fields populated, **0** tokens streamed, 8 grounding spans |
| Draft | 3,083 chars, 13 `[ATTORNEY INPUT REQUIRED]` markers, 6 spans |
| Audit | 5 rows, correct `retrieval_path` per intent (`whole_document` for summary), the abstained turn `grounded=True` with 0 spans |
| Cost | 13 cost rows across 3 models, **$0.1163** for the run |

**Prompt caching works on the generation calls, and §9.2's caveat is still right.**
The first `ask` wrote a 2,254-token prefix and the next two read it — a full hit
including the documents, not just the system prompt. But that is because this corpus
chunked to one passage, so retrieval returned identical pages every time. §9.2's
reasoning holds: vary the retrieved set and the document part of the prefix stops
matching. A small corpus is the best case, not the normal one.

**The routing steps are the uncached ones, and they are not cheap.** `intent` and
`rewrite` go through LangChain with no cache breakpoint, so each re-sends its whole
prompt:

| step | calls | input tokens each | cached |
|---|---|---|---|
| `intent` | 5 | ~2,612 | never |
| `rewrite` | 3 | ~2,306 | never |
| `generation:*` | 5 | 14–21 + cache | yes |

That is ~20K uncached Haiku input tokens, **$0.0202 of the $0.1163 run — about 17%**,
spent entirely on re-sending two static system prompts. The generation calls, which
carry whole contracts, cost less in input than the routers do. A cache breakpoint on
the `intent` and `rewrite` system prompts is the obvious fix and is not done: both
calls are LangChain `with_structured_output` chains, where the breakpoint goes in
differently than in the raw SDK path `generate.py` uses.

**One cosmetic rough edge.** `title_for()` is mechanical by design, so the draft came
back titled *"Draft a short memo on the termination provisions"* — the request, not a
title. Deliberate (§`draft.py`: "spending a model call to name a document the attorney
is about to retitle would be a cost with no reader"), and it does read oddly on the
rendered memo.

---

### Still open

- The reranker measurement wants re-running on a corpus where candidates < corpus
  (§9.2), and now also counting pages rather than chunks — see above.
- ~~The routing-cost finding~~, ~~`title_for()`~~ — **both done 2026-10-19, §9.4.**
  The routing fix was *not* the cache breakpoint this section prescribed; see below
  for why that would have done nothing.

---

## 9.4 Gap pass — 2026-10-19

Six gaps, found by running the integrated service against a generated 28-page MSA
rather than by reading the code. Each one was measured before and after.

### §1 was decided wrong, and FR-13 was the price

`ask` always retrieved, and on a 28-page contract that meant `ask_top_n` chunks of
twenty-eight pages, so `coverage` was **false on every question** and the prompt
forbade the model from reporting an absence. Measured: *"Is there a non-compete
clause?"* answered

> *"The excerpts retrieved for this question do not address it. This is not a finding
> that the documents are silent."*

on a document that fits one prompt **eight times over**. That is not a safe
abstention, it is FR-13 failing — and §9.2's claim that the coverage flag "handles
the absence risk without a threshold" is only true in the sense that it stops the
model lying. It does not make the question answerable.

`Retriever.select()` now makes the §4.2 decision this plan recommended in §1 and the
build skipped: at or below `whole_document_max_tokens` (120K, per §1's
recommendation) the whole corpus goes to the model; above it, hybrid retrieval and
the reranker, exactly as before. Same question, after:

> *"The documents in scope do not address this question. No answer was inferred from
> outside them."*

A finding about the documents, which is what FR-13 asks for. Two side effects worth
recording:

- **Latency collapsed.** `ask` went from 45.2s to 8.4s, and the absence question from
  59.8s to 3.2s — the whole-document route skips the ~2.5s reranker and the query
  embed, and the document prefix caches across turns in a conversation.
- **The hybrid pipeline is now dormant on a typical contract**, which is the cost §1
  named and accepted. `ORCH_WHOLE_DOCUMENT_MAX_TOKENS=0` restores always-retrieve in
  one variable, and it is the configuration every §9.2 measurement was taken under.
  Lower the budget to exercise the retrieval arm on realistic corpora.

### The prescribed cache fix would have cached nothing

§9.3 says *"a cache breakpoint on the `intent` and `rewrite` system prompts is the
obvious fix"*. It is not: **Anthropic's minimum cacheable prefix is per-model and not
monotonic across generations.** Haiku 4.5 requires **4096 tokens**. Measured with
`count_tokens`, the two prompts are **1742** and **1274**. A breakpoint on either
would have reported `cache_creation_input_tokens: 0` and no error — the failure mode
is silence.

Haiku 5.5's minimum is **512**, and its input is **a tenth of the price**. So the fix
is the model, and then the breakpoint. Both suites were re-run on it before it landed:
**intent 25/25, rewrite 15/15** — unchanged. Two API constraints came with it: a
non-default `temperature` is a 400 (so `temperature=0` is gone from both; the enum in
the tool schema is what actually bounds the answer), and `effort` is still rejected.

Measured over the same five-turn run:

| step | before | after |
|---|---|---|
| `intent` | 13,066 input tokens, **0 cached**, $0.01516 | 1,102 fresh + 11,472 cached, **$0.00089** |
| `rewrite` | 7,621 input tokens, **0 cached**, $0.00890 | 3,096 fresh + 4,450 cached, **$0.00081** |
| routing as a share of the turn | **7.0%** | **0.53%** |

A 93% cut on the routing steps. One trap found on the way: LangChain's
`usage_metadata["input_tokens"]` **includes** cache reads and writes, while
Anthropic's excludes them and `pricing.cost_usd` adds all four together. Recording
LangChain's figure next to the cache counts bills every cached token twice — so a
cost table would have got *more* expensive as caching started working.
`orchestrator/usage.py` does the subtraction and explains why.

### Retrieval provenance was computed and then dropped

§5 of this plan asks for the rewrite to be *"recorded on the audit row"*. It was
logged to stdout and never persisted; `coverage` — the flag that licensed or forbade
a claim of silence — was computed, carried as far as `RetrievalReport`, and dropped
by the runner. So the row for an abstained turn could not say whether the abstention
was a finding or a limitation, which is the single most important thing about it.

`audit_log` gains `coverage` and `rewrite` (both nullable — `None` is "this provider
does not retrieve", which is not `False`/"no rewrite"), carried on `RetrievalReport`,
exposed on `AuditEntry`. The follow-up turn in the run above now records:

> `'And how long is it capped for?'` → `'How long is the limitation of liability in
> this agreement capped for, i.e., over what look-back period is the cap measured?'`

Which is also the first visible evidence of a **mild prompt violation worth
watching**: the guide says never broaden what was asked, and the trailing clause is
broader than the question. The answer was right, and the suite is 15/15, so this is a
note rather than a finding — but it is the kind of thing that was previously
invisible, which is the point of recording it.

### Owner scoping is now structural

§9.3 left this as Phase 2 work: *"`PassageIndex` carries no owner in its payload and
filters by document id ... currently one correct call site, not defence in depth."*
`Passage.owner_id` is now in the Qdrant payload and `_scope_filter` makes it a `must`
condition beside the document ids, so a bug in the caller that resolves the document
set yields no results rather than another tenant's contract. `PageStore.scoped_to`
applies the same narrowing to the page text, which lives outside Qdrant and had the
same exposure. The owner reaches the provider on `GenerationRequest` and is bound
once at the edge (`_OwnerScoped`), so no workflow has to remember it.

Pinned by a real two-tenant index: a search that names *both* tenants' documents in
scope returns only the requesting owner's.

### The cost table was write-only

`usage_costs` has been written since the first turn and `AuditRepository.total_cost()`
had **no callers** — read by nothing but tests. That is how the routing steps came to
be 7% of a turn without anyone noticing. `GET /audit` now returns `totalCostUsd` and
`GET /audit/costs` breaks spend down by `purpose`. A number nobody can read is not a
measurement.

### The frontend ignored the backend's `error` frame

`api/chat.py` sends `event: error` when generation fails after the stream has opened,
then `done`. `lib/sse.ts` had no branch for it: the frame fell through, `done` cleared
the spinner, and a failed turn was indistinguishable from a short one. There is now a
`StreamError`, a `GenerationErrorNotice` styled as a failure (deliberately unlike
`AbstentionNotice`, which is the product working), and a retry that reuses the
question and scope.

### `title_for()`

*"Draft a short memo on the termination provisions"* → **"Memo on the termination
provisions"**. Still mechanical, still no model call. The rule is narrow on purpose —
drop the imperative and its filler, keep the noun phrase. An earlier attempt also ate
the document noun and turned *"Prepare an email to the counterparty"* into
*"Counterparty"*, losing what the document was.

### Still open after this pass

- ~~The reranker re-measurement~~, ~~the summary/draft cache writes~~ — **both done
  2026-10-19, §9.5.**
- The rewrite broadening noted above.

---

## 9.5 Reranking, re-measured — and the cache bill — 2026-10-19

### The reranker measurement was wrong, and it was wrong in our favour

§9.2 measured reranking as a **regression** (recall@1 0.67 → 0.64) and kept the step
on anyway, on the argument that the measurement's regime was not the production one.
That argument was right and it was also unfalsifiable with the eval as it stood: 25
candidates per arm against a 25-page corpus means fusion hands the reranker
**everything**, and a reranker given the whole corpus is doing a different job from
one given the top 25 of two hundred.

`tests/eval/filler.py` fixes the eval rather than the argument. It adds 80 pages of
general provisions across four more agreements — not padding, because **a real
contract is mostly general provisions**, and the question a production reranker faces
is whether it can tell a clause that answers the question from forty pages of
plausible, well-drafted, entirely irrelevant legal English. The corpus is now 105
pages and 25 candidates is 24% of it. `run_spike.py` prints the regime on every run,
so a future measurement cannot quietly be taken in the wrong one again.

Two automated guards came with it, because "I was careful when I wrote the filler" is
precisely the assurance that failed when a distractor silently introduced force
majeure and invalidated four absence questions. `validate.py` now refuses any filler
that contains a scored question's expected phrase, or that names a subject the
question set asks about. **It caught one on its first run** — the INTERPRETATION
clause said "for convenience of reference", which is `q09`'s expected phrase. It
would not have answered q09, but a guard that has to judge intent is a guard that
lets the real mistake through, so the filler was reworded instead.

Re-measured on `bge-m3`, 18 scored questions, 25 candidates per arm:

| reranker | @1 | @3 | @8 | MRR | wrong first | per query |
|---|---|---|---|---|---|---|
| none (fusion order) | 0.56 | 0.72 | 0.89 | 0.75 | 6/18 | — |
| ms-marco-MiniLM-L-6-v2 | 0.56 | 0.83 | 0.92 | 0.79 | 6/18 | ~150ms |
| bge-reranker-base | 0.33 | 0.67 | 0.92 | 0.60 | 11/18 | ~1000ms |
| **bge-reranker-v2-m3** | **0.64** | **0.83** | **0.92** | **0.84** | **4/18** | ~3550ms |

**The step now wins on every metric**, where before it had to be argued for. Three
findings behind the table:

1. **No reranking degrades as the corpus grows** — 0.67 → 0.56 — while reranking holds
   at 0.64. The reranker's value is a function of how much there is to get wrong, so
   the small-corpus measurement did not merely mis-measure it, it was *structurally
   biased against it*, and any eval smaller than a real matter will be.
2. **`collision` goes 0.50 → 1.00.** That is precisely the job a cross-encoder exists
   for: separating clauses whose words match a question they do not answer.
3. **`paraphrase` goes 0.58 → 0.33, and every reranker measured moves it the same
   way.** A question phrased in none of the document's words is found by the dense arm
   and then demoted by the cross-encoder. This is the real cost of the step, it is
   consistent, and nothing here fixes it — it is the next measurement worth taking.

`ms-marco` also needs its earlier reading withdrawn: at 25 pages it looked like noise,
and here it is +0.11 recall@3 and +0.04 MRR for **150ms against 3550ms**. If latency
ever stops being deprioritised, that is the comparison to run properly.

### 62.6% of a turn's cost was cache writes, and two thirds of them were never read

Decomposed from the §9.4 run:

| component | cost | share |
|---|---|---|
| **cache writes** | **$0.20206** | **62.6%** |
| output tokens | $0.11203 | 34.7% |
| cache reads | $0.00814 | 2.5% |
| fresh input | $0.00069 | 0.2% |

A cache write costs **1.25×** the input rate and a read **0.1×**. So writing a prefix
that is never read again is a flat 25% surcharge on the largest part of the request —
and `cache_last` applied the breakpoint unconditionally, to all three workflows. The
same ~20K-token document prefix was written three times, once per workflow (the system
prompt precedes the documents in the cache prefix and differs per workflow, so the
three can never share one entry), and **two of those three writes were read zero
times**: summary and draft each produce one artifact from one call.

It is now a decision per workflow:

- **`ask` — keep.** A conversation asks several questions of the same corpus, and on
  the whole-document route the prefix is byte-identical between turns. Measured: one
  19,955-token write served 39,910 tokens of reads.
- **`draft` — drop.** One call, no retry pass, no possible reader.
- **`summary` — drop,** and this one is arithmetic rather than obvious, because the
  retry pass *would* read it. The write costs 0.25× the corpus every time; the saving
  is 0.90× of it on the summaries that retry. Break-even is a **retry rate above
  ~25%**, and both end-to-end runs populated 9/9 headings on the first pass.
  `summary.CACHE_DOCUMENTS` records the sum and the figure to watch — the ratio of
  `summary:retry` to `summary` rows, which `GET /audit/costs` now reports.

The saving is exact rather than estimated, because the token counts do not change —
only the rate those tokens are billed at:

| | before | after | saved |
|---|---|---|---|
| draft documents | 20,200 @ $5.00/MTok | 20,200 @ $4.00/MTok | $0.0202 |
| summary documents | 20,215 @ $2.50/MTok | 20,215 @ $2.00/MTok | $0.0101 |
| **run total** | **$0.32293** | **$0.29263** | **$0.0303 — 9.4%** |

That 9.4% is a like-for-like figure: the same request, the same tokens, a different
rate. The routing fix in §9.4 was worth a further $0.0224 on the same run shape, so
the two together take a five-turn run from about **$0.343 to $0.293**. Treat the
combined number as approximate rather than measured — the two runs it spans produced
different amounts of output (draft 3,181 tokens against 2,493; summary 4,904 against
5,171), because model output varies between runs and only the rate changes are exact.

### Not done: effort

Output tokens are the remaining 34.7%, and at effort `high` much of that is thinking —
summary produced 5,171 output tokens for nine fields. `medium` is the obvious lever
and **it is not taken here, because it was not measured**: changing an effort default
on the strength of expectation is the one move the API guidance is explicit about not
making. The sweep to run is summary and draft at `high`/`medium`/`low` over one real
corpus, scored on fields populated, citation count and marker count — if `medium`
holds those, it is roughly another 10–15% for nothing.

## 10. Questions

1. **§1 — whole-document vs always-retrieve.** The one that changes the architecture.
   My recommendation: keep the threshold, lower it to ~120K.
2. **Reranker on CPU** — if step 1's spike shows the cross-encoder costs several
   seconds per question on your hardware, do you want it anyway (you've deprioritised
   latency), or should I plan a smaller reranker?
3. **Evaluation corpus** — do you have real contracts I should build the eval set
   from, or should I generate representative ones? The eval is what tells us any of
   this helped.
