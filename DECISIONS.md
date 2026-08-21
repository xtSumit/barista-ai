# Decisions log — AI Barista

Why this project is built the way it is. Deliberately terse: the decision, the
reason, and what would change it. A few entries record something that went wrong.

Detail lives in the code and `README.md`; this file exists to answer "why?" quickly.

---

**D1 — No Firestore, no embeddings. Retrieval reads `menu.json`.**
On eight items, keyword matching retrieves as well as a vector index would, and
Firestore would add a database, an embedding call per question, and an IAM role for
no change in grounding. *Revisit at ~50+ items, or if semantic matches are wanted
("something to wake me up" → Cold Brew).*

**D2 — API key locally, service-account Vertex AI on Cloud Run.**
No gcloud CLI on the dev machine, so no Application Default Credentials — a key is
the only local option. In the cloud a service account is free, so no credential
exists to leak. The SDK reads its backend from env vars, so **no code branches**.
*Cost: needs the Vertex API enabled and `roles/aiplatform.user` granted.*

**D3 — `menu.json` opened relative to `agent.py`, not the CWD.**
`open("menu.json")` works only if the process starts in the project root. Cloud Run's
buildpack launcher doesn't guarantee that, and the failure is ugly: deploys green,
then every recommendation dies on `FileNotFoundError`.

**D4 — The model name is an env var, not a literal.**
Normally a constant that never changes shouldn't be configurable. This one earned it:
see D10. Four model changes, zero code edits.

**D5 — One `test_menu.py`, plain asserts, no pytest.**
Retrieval is the only non-trivial logic, and the allergen filter is where a bug is
actually dangerous. A framework would be more scaffolding than code under test.
Runs offline: no key, no network, no cost.

**D6 — Retrieval lives in its own module.**
`menu_tool.py` imports nothing from Google, so its test needs no credentials. If the
logic sat in `agent.py`, testing a keyword scan would require building an `LlmAgent`.

**D7 — `run_async()`, not the codelab's `run_debug()`.**
`run_debug`'s own docstring says debugging-only and points to `run_async` for
production. This gets a public URL. It also exposes the individual tool events —
which is what powers the "Grounded in N menu items" disclosure. *Cost: ~15 lines.*

**D8 — The session is created explicitly.**
`Runner.auto_create_session` defaults to `False`, so `run_async` with a fresh session
id raises `SessionNotFoundError` — the app would have crashed on its first message.
`run_debug` hid this by doing it internally.

**D9 — One long-lived event loop on a daemon thread.**
`asyncio.run()` per message closes the loop its API client is bound to, so the first
message works and the **second** fails with "Event loop is closed". Streamlit also
re-runs on different threads, hence `run_coroutine_threadsafe` rather than
`run_until_complete`. *Verified with three messages from three threads.*

**D10 — Model: `gemini-3.5-flash`, after four attempts.**
`2.5-flash` is retired for new API keys (404); `3.6-flash` and `3.7-flash` both
rate-limited. Not an alias like `flash-latest`: a model that moves under a graded
submission is a risk, not a convenience.

**D11 — The tool reports unsafe drinks instead of hiding them.**
The filter removed unsafe drinks so completely that the agent couldn't tell "we don't
sell it" from "it would hurt you" — asked about hazelnut, it said *"we don't have
any"*, which is false. Now `search_menu` also returns `unsafe_matches`, named
honestly and never offered. *The one failure mode here with consequences off-screen.*

**D12 — Quota errors get plain English; everything else shows its real text.**
A traceback in the chat window reads as a broken app. Swallowing genuine errors would
make it undebuggable, so only quota is special-cased.

**D13 — Quota is 20 requests/day/model, and retries don't fix it.**
One chat turn costs ~2 requests, so the free tier is 8–10 turns per model per day.
The ADK docs suggest client-side retries; useless against a daily cap, and they'd
hide the cause behind a spinner. Deliberately not added. *Doesn't affect Cloud Run —
Vertex bills against project quota.*

**D14 — A silent failed edit.**
The D12 message was reported as written but wasn't: `str.replace()` on a
non-matching pattern returns the string unchanged instead of raising. **Check the
file, not the report** — any pattern-based edit must assert the pattern matched.

**D15 — No automatic model failover.**
Tempting after D13, but it's compensating code for a free-tier limit the deployed app
doesn't have, and failover belongs in a gateway, not in `app.py`. The real objection:
the allergen *filter* is deterministic, but the model must still **pass** the allergy
each turn, and swapping models silently varies how reliably it does — quality
variance on the one safety-relevant path. It also destroys a stable cost per request.
*Revisit with real traffic and an availability target: then a router, a pinned and
tested model set, log which model served each request, and alert on the fallback rate
rather than hiding it.*
