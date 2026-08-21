# Decisions log — AI Barista (ADK + RAG on Cloud Run)

Why this project is built the way it is: every non-obvious call, what it cost, and
when to revisit it. Written as it was built, so the mistakes are in here too — a few
entries record something that went wrong and what replaced it.

Newest entries at the bottom.

---

## D1 — No Firestore, no vector embeddings. RAG reads `menu.json` directly.

The reference codelab offers an optional Firestore path with vector search over
embedded menu items. Dropped.

**Why:** the grading asks for responses grounded in a menu dataset. With 8 items,
a keyword scan over name/description/tags retrieves exactly as correctly as a
vector index would — an embedding model cannot beat exact matching on a corpus
this small. Firestore would add a database, an embedding API call per query, an
extra IAM role, and a seeding script, for zero difference in what the barista says.

**When to revisit:** if the menu grows past roughly 50 items, or if you want the
agent to match on meaning rather than words ("something to wake me up" finding
Cold Brew with no shared keyword). At that point add embeddings, and Firestore
only if you also need the menu editable without a redeploy.

---

## D2 — Auth: AI Studio API key locally, Vertex AI service account on Cloud Run.

Local `.env` holds `GOOGLE_API_KEY`. Cloud Run instead gets
`GOOGLE_GENAI_USE_VERTEXAI=TRUE` plus project and location, and authenticates as
its runtime service account.

**Why:** you have no gcloud CLI locally, so Application Default Credentials are
not obtainable on your machine — an API key is the only local option. In the
cloud the opposite is true: a service account is available for free and means no
key exists in the deployed config to leak or rotate. The `google-genai` client
picks its backend up from environment variables, so **no code branches on this**.
Same `agent.py` and `app.py` in both places; only the environment differs.

**The rejected alternative:** using the API key in both places is one less concept
to explain, but it puts a long-lived credential into the Cloud Run service config,
and the codelab this is graded against uses the Vertex path.

**What this costs you:** the deploy needs the Vertex AI API enabled and the
service account granted `roles/aiplatform.user`. Both are in the README's
Cloud Shell block.

---

## D3 — `menu.json` is opened relative to `agent.py`, not the working directory.

`Path(__file__).parent / "menu.json"` rather than `open("menu.json")`.

**Why:** the codelab's version works only when the process happens to start in the
project root. Cloud Run's buildpack launcher does not guarantee that, and the
failure mode is ugly — the container builds and deploys green, then every single
recommendation fails at runtime with `FileNotFoundError` because the agent cannot
read its own menu. One `Path` call removes the whole class of bug.

---

## D4 — The Gemini model name is an environment variable, not a literal.

`os.getenv("BARISTA_MODEL", "gemini-2.5-flash")`.

**Why:** normally a value that never changes should not be configurable. This one
is genuinely uncertain: the codelab page names a model string I could not verify
against the installed SDK, and a wrong model name fails at the first message with
a 404. As an env var, correcting it is a one-word change in `.env` or in the
deploy command — no code edit, no redeploy of changed source.

---

## D5 — One `test_menu.py` with plain asserts. No pytest, no fixtures.

**Why:** the retrieval function is the only non-trivial logic in the project, and
the allergen filter is the part where a bug is actually dangerous rather than just
wrong. That deserves a check that fails loudly. It deserves exactly one — a test
framework, config, and directory layout would be more scaffolding than code under
test. Runs offline with `python test_menu.py`: no API key, no network, no cost.

---

## D6 — Retrieval lives in `menu_tool.py`, separate from `agent.py`.

**Why:** it makes `test_menu.py` runnable with no ADK import, no API key and no
network. If the retrieval logic sat inside `agent.py`, testing it would mean
constructing an `LlmAgent` first, and the test would start depending on
credentials to check a keyword scan. One extra file buys a test that always runs.

---

## D7 — `run_async()`, not `run_debug()`.

The codelab uses `runner.run_debug(prompt, session_id=...)`. I read the installed
ADK 2.2.0 source instead of copying it, and `run_debug`'s own docstring says:
*"This is for debugging and experimentation only. For production use, please use
the standard run_async() method."* It also prints to stdout and hides event
streaming — both wrong for a Streamlit UI.

**What it cost:** about fifteen more lines in `app.py` to iterate events myself.
**What it bought:** access to the tool-call events, which is how the "Grounded in
N menu items" expander can show what RAG actually retrieved.

---

## D8 — The Streamlit session must be created explicitly.

`Runner.__init__` has `auto_create_session: bool = False`. So calling
`run_async()` with a fresh session id raises `SessionNotFoundError` — the app
would have crashed on the very first message.

**Why this is worth writing down:** the codelab never mentions it, because
`run_debug()` handles session creation internally. The moment you move to
`run_async()` you inherit the responsibility. `_ensure_session()` in `app.py`
does a `get_session` then `create_session` if absent.

---

## D9 — One long-lived event loop on a background thread.

`app.py` starts a single `asyncio` loop in a daemon thread (cached by
`st.cache_resource`) and submits work with `asyncio.run_coroutine_threadsafe`.

**Why not the obvious `asyncio.run(...)` per message:** `asyncio.run` closes its
loop when it returns, but the cached genai client keeps connections bound to the
loop that created them. The first message works and the **second** one fails with
"Event loop is closed" — the worst kind of bug, because the smoke test passes and
the demo dies on the follow-up question.

**Why not just `st.cache_resource` on a plain loop:** Streamlit runs each re-run
on a possibly different thread, and `run_until_complete` from a foreign thread
isn't safe. `run_coroutine_threadsafe` is designed for exactly this.

Verified with a throwaway script that sent three messages from three different
threads through this exact pattern.

---

## D10 — Model: `gemini-3.5-flash`. This took four tries and the env var earned itself.

1. `gemini-2.5-flash` (my initial default) → **404**: *"no longer available to new
   users"*. Retired for new API keys.
2. `gemini-3.6-flash` (what the 404 recommended, near the codelab's `3.5-flash`)
   → worked, then **429 RESOURCE_EXHAUSTED** / **503**. Tight free-tier quota.
3. `gemini-3.7-flash` → answered 3/3 on a direct probe, then started returning
   **503** through ADK a few minutes later.
4. `gemini-3.5-flash` → reliably clean across every test, including the full
   3-turn conversation. This is the default.

The quota is **per model**, not per project: with `3.6` and `3.7` both throttled,
`3.5` kept answering on the same key in the same minute. Worth knowing before you
demo — the 503 message says "high demand", but the cause is usually your quota on
that specific model, and the fix is another model rather than waiting.

**Why this vindicates D4:** four model changes, zero code edits. Switching is
`BARISTA_MODEL=gemini-3.7-flash streamlit run app.py`.

**Why not `gemini-flash-latest`:** an alias that silently moves under a graded
submission is a risk, not a convenience. Pin it.

**On Cloud Run this matters less** — Vertex AI bills against project quota rather
than the AI Studio free tier.

---

## D11 — The tool reports unsafe drinks instead of hiding them.

Found by reading the first live transcript, not by testing. Asked "do you have
anything with hazelnut?" by a lactose-intolerant customer, the agent said *"We
don't currently have any drinks with hazelnut."* That is false — we serve a
Hazelnut Mocha; it just contains dairy. The allergen filter had removed it so
completely that the agent couldn't distinguish "we don't sell it" from "it would
hurt you".

**The fix:** `search_menu` now also returns `unsafe_matches` — drinks that matched
the query but were filtered, with their allergens. The instruction requires
naming them honestly and never offering them. The same question now answers:
*"We do serve a Hazelnut Mocha, but it contains dairy, so it isn't safe with your
lactose intolerance."*

**Why this wasn't optional:** a customer told "we don't have that" may go looking
for it elsewhere. Being wrong about an allergen is the one failure in this app
with consequences outside the screen.

---

## D12 — Rate-limit errors get a plain-English message, not a stack trace.

`app.py` catches 429/503/RESOURCE_EXHAUSTED and tells the user the machine is
backed up, naming the model and the `BARISTA_MODEL` escape hatch. Every other
exception still shows its real text — swallowing genuine errors would make this
undebuggable.

**Why:** given how easily the free tier throttles (see D10), a grader clicking
around is reasonably likely to hit it. A traceback in the chat window reads as a
broken app; a sentence reads as a busy one.

---

## D13 — The real quota shape: 20 requests per day, per model. Retries don't help.

D10 called this "per-model quota" without a number. The precise error names it:

```
quotaId:     GenerateRequestsPerDayPerProjectPerModel-FreeTier
quotaValue:  20
model:       gemini-3.5-flash
retryDelay:  50s
```

**Per DAY, per model, per project.** And one chat turn costs about two requests —
one where the model decides to call `search_menu`, one where it writes the answer
from the result. So the free tier is roughly **8–10 turns of conversation per model
per day**, which is why testing burned through it so fast.

**Why the ADK docs' advice doesn't apply here.** The official mitigations are
"request higher quota" or "enable client-side retries". Retries are the wrong tool
for a daily cap: the `retryDelay: 50s` is generic, and waiting 50 seconds only buys
one more request out of a bucket that is already empty until the day resets. Blind
retries would also hide the real cause behind a slow spinner. Deliberately not added.

**What actually works, cheapest first:**

1. Switch model — the cap is per model, so each one has its own 20.
2. Use `test_menu.py` for logic work; it costs nothing.
3. Paid tier for unrestricted local development.

**And the part worth remembering: none of this affects the deployed app.** Cloud Run
authenticates through Vertex AI, which bills against project quota, not the AI Studio
free tier. The cap is a local-development constraint only.

---

## D14 — A silent failed edit, and the check that would have caught it.

The friendly rate-limit message described in D12 **was never actually in `app.py`**.
The edit used `str.replace()` on a pattern that didn't match, and `str.replace`
returns the string unchanged rather than raising — so it reported success and wrote
the file back untouched. The raw traceback stayed in place, which is what the user
saw when they hit the quota.

**The lesson, generalised:** an edit that cannot fail is an edit that cannot be
trusted. Anything that rewrites a file by pattern needs to assert the pattern
matched, or use a tool that errors on a miss. Checking the *report* is not checking
the *file*.

Fixed by re-reading the actual file contents and editing against them, then grepping
to confirm the new code is present.
