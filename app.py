"""Streamlit chat UI for the ADK barista agent."""

import asyncio
import queue
import threading
import uuid

import streamlit as st
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.runners import InMemoryRunner
from google.genai import types

from agent import MODEL, MODEL_FALLBACKS, app as adk_app
from drink_art import BARISTA_MARK, art_for
from menu_tool import _load_menu

st.set_page_config(
    page_title="AI Barista", page_icon=":material/local_cafe:", layout="centered"
)

# Material Symbols rather than emoji, so the chat reads like a person talking.
# None gives the customer Streamlit's own default person icon.
BARISTA_AVATAR = ":material/local_cafe:"
CUSTOMER_AVATAR = None

GREETING = (
    "Morning. I'm the barista here — tell me what you're in the mood for and "
    "I'll find you something."
)

STARTERS = [
    "Something strong and iced",
    "I'm lactose intolerant",
    "Something sweet and cold",
    "Surprise me",
]

# Shown one after another while the barista is working, so a slow first token
# reads as someone making a drink rather than a hung page. The order follows how
# a drink is actually made; the last one holds if it takes longer than the list.
BREWING_STEPS = [
    "Checking the menu…",
    "Grinding the beans…",
    "Tamping the shot…",
    "Pulling the shot…",
    "Steaming the milk…",
    "Pouring…",
]

# Streamlit owns the layout; this only softens what it already draws. Kept small
# on purpose — deep selectors into Streamlit's DOM break on version upgrades.
CSS = """
<style>
  /* Earthen tints, defined once. Kept as plain values rather than reading
     Streamlit's own CSS variables, which are not part of its public API. */
  :root {
    --ink: #38302A;          /* dark umber */
    --clay: #A2705A;         /* terracotta */
    --card: #EFE7DA;         /* fired clay, a shade off the sidebar */
    --edge: rgba(56, 48, 42, 0.16);
    --moss: #6E6B4F;         /* dried olive, for the quiet second line */
    --sand: #E5DBCB;         /* clay-sand — the sidebar, and now the button */
    --input-h: 57.45px;      /* measured: Streamlit's chat input, serif theme */
  }
  /* Square edges and hairlines rather than rounded, filled boxes. */
  .stChatMessage { border-radius: 3px; padding: 0.75rem 1rem; overflow-wrap: anywhere; }
  .stChatInput textarea { border-radius: 3px; }
  /* The input is white, so it reads as a clean sheet against the warm ground.
     Streamlit paints the fill on its own container, so it is overridden there
     and the inner wrappers are made transparent, or the old colour shows
     through underneath. The hairline keeps a white box from floating. */
  /* Every div in the chain gets the white, not just the outer container and not
     via transparency: Streamlit nests several wrappers inside the chat input and
     paints the fill on one of the inner ones, so a single-level rule leaves a
     coloured box behind the placeholder. Buttons are left alone deliberately —
     the send arrow keeps its own styling. */
  .st-key-inputrow .stChatInput,
  .st-key-inputrow .stChatInput div,
  .st-key-inputrow .stChatInput textarea {
    background-color: #FFFFFF !important;
  }
  .st-key-inputrow .stChatInput {
    border: 1px solid var(--edge) !important;
    border-radius: 3px !important;
  }
  div[data-testid="stChatMessageAvatarUser"],
  div[data-testid="stChatMessageAvatarAssistant"] { font-size: 1.15rem; }
  section[data-testid="stSidebar"] { width: 24rem !important; }
  section[data-testid="stSidebar"] h3 { margin-bottom: 0.1rem; font-weight: 500; }

  /* The barista mark sits on the baseline of the title rather than above it. */
  .hdr { display: flex; align-items: center; gap: 0.65rem; margin: 0 0 0.15rem; }
  .hdr svg { color: var(--clay); flex: 0 0 auto; }
  .hdr h1 { margin: 0; padding: 0; font-weight: 600; letter-spacing: 0.01em; }

  /* One menu tile, now a card: drawing, name, then price and allergens. */
  .drink {
    background: var(--card);
    border: 1px solid var(--edge);
    border-radius: 3px;
    padding: 0.6rem 0.7rem 0.55rem;
    margin-bottom: 0.55rem;
    min-height: 7.4rem;
  }
  .drink svg { color: var(--clay); opacity: 0.85; margin-bottom: -0.1rem; }
  .drink-name { font-weight: 600; line-height: 1.25; color: var(--ink); }
  .drink-price { font-size: 0.78rem; color: var(--moss); }
  .drink-meta { font-size: 0.72rem; color: var(--moss); opacity: 0.85; line-height: 1.4; }

  /* Start over shares the pinned row with the chat input and has to match its
     height. Inheriting that height through the flex/grid box did not survive
     Streamlit's own rules, so the input was measured in the browser instead and
     both are pinned to one value. --input-h is the chat input's rendered height
     at this theme's font: re-measure it (DevTools, the element with
     data-testid="stChatInput", Computed > height) if the font or base size
     changes, because the input's height follows those. */
  .st-key-inputrow {
    display: grid !important;
    grid-template-columns: 1fr auto;
    align-items: center !important;
    gap: 0.5rem;
  }
  .st-key-inputrow > div { margin: 0 !important; }
  .st-key-startover button {
    height: var(--input-h) !important;
    min-height: var(--input-h) !important;
    width: 100%;
    white-space: nowrap;
    /* Takes the colour the input used to have, and rounder corners than the
       squared-off cards elsewhere. */
    background: var(--sand) !important;
    border: 1px solid transparent !important;
    border-radius: 10px !important;
    color: var(--ink) !important;
  }
  .st-key-startover button:hover { border-color: var(--clay) !important; }
</style>
"""

_SENTINEL = object()

# Longest wait for the *next* chunk, not for the whole reply — so a slow but
# healthy stream is never cut off, while a dead one fails visibly.
STREAM_TIMEOUT_S = 90

# How often to check in while waiting for the next chunk. Only sets how promptly
# the brewing caption advances — not how long a slow reply is given.
POLL_S = 0.8


@st.cache_resource
def get_runner() -> InMemoryRunner:
    """One runner for the process. Holds the in-memory session store, so it must
    survive Streamlit's re-run-the-whole-script-on-every-interaction model."""
    return InMemoryRunner(app=adk_app)


@st.cache_resource
def get_loop() -> asyncio.AbstractEventLoop:
    """One long-lived event loop on a background thread.

    Streamlit runs each re-run on a possibly different thread, and the genai
    client keeps connections bound to the loop that created them. A fresh
    asyncio.run() per message would close that loop underneath the client and
    fail on the *second* message. A single persistent loop, fed thread-safely,
    sidesteps both problems.
    """
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True, name="adk-loop").start()
    return loop


def next_model() -> str:
    """The model to suggest once this one's quota is gone — the next one along,
    wrapping, so the message never recommends the model that just failed."""
    try:
        i = MODEL_FALLBACKS.index(MODEL)
    except ValueError:
        return MODEL_FALLBACKS[0]
    return MODEL_FALLBACKS[(i + 1) % len(MODEL_FALLBACKS)]


def friendly_error(exc: Exception) -> str:
    text = str(exc)
    if "RESOURCE_EXHAUSTED" in text or "429" in text or "UNAVAILABLE" in text:
        # The free tier caps requests per DAY per model, so "wait and retry" is
        # the wrong advice — switching model is what actually works.
        daily = "PerDay" in text or "per day" in text.lower()
        detail = (
            "is used up for today. The free tier allows 20 requests per day per "
            "model, and each turn of chat costs about two."
            if daily else
            "was hit. This one is usually a short burst limit — try again shortly."
        )
        return (
            f"Sorry, the coffee machine is backed up. Gemini quota on `{MODEL}` "
            f"{detail}\n\nEvery model has its own daily quota. To switch, set "
            f"`BARISTA_MODEL={next_model()}` in `.env` and restart the app — the "
            "full list of alternatives is in that file."
        )
    # Anything else is a real bug — show it rather than hide it.
    return f"Something went wrong talking to Gemini:\n\n`{exc}`"


async def ensure_session(runner: InMemoryRunner, session_id: str, user_id: str) -> None:
    # auto_create_session defaults to False, so run_async would raise
    # SessionNotFoundError on the first message without this.
    existing = await runner.session_service.get_session(
        app_name=runner.app_name, user_id=user_id, session_id=session_id
    )
    if existing is None:
        await runner.session_service.create_session(
            app_name=runner.app_name, user_id=user_id, session_id=session_id
        )


def stream_answer(runner, prompt: str, session_id: str, user_id: str, on_wait=None):
    """Yield the barista's reply in chunks, for st.write_stream.

    on_wait, if given, is called for every POLL_S seconds that pass with no new
    chunk — the hook the brewing caption advances on.

    The ADK stream is async and lives on the background loop; st.write_stream
    needs a plain generator on Streamlit's own thread. A queue bridges the two:
    the coroutine pushes text deltas in, this generator drains them out.
    """
    chunks: queue.Queue = queue.Queue()

    async def produce():
        streamed_any = False
        try:
            await ensure_session(runner, session_id, user_id)
            message = types.Content(role="user", parts=[types.Part(text=prompt)])
            config = RunConfig(streaming_mode=StreamingMode.SSE)
            async for event in runner.run_async(
                user_id=user_id,
                session_id=session_id,
                new_message=message,
                run_config=config,
            ):
                if not (event.content and event.content.parts):
                    continue
                for part in event.content.parts:
                    text = getattr(part, "text", None)
                    if not text:
                        continue
                    if event.partial:
                        streamed_any = True
                        chunks.put(text)
                    elif not streamed_any:
                        # Non-streaming fallback: the final event carries the whole
                        # reply. Skipped when deltas already arrived, or the answer
                        # would appear twice.
                        chunks.put(text)
        except Exception as exc:
            chunks.put(("\n\n" if streamed_any else "") + friendly_error(exc))
        finally:
            chunks.put(_SENTINEL)

    future = asyncio.run_coroutine_threadsafe(produce(), get_loop())

    waited = 0.0
    while True:
        try:
            item = chunks.get(timeout=POLL_S)
        except queue.Empty:
            waited += POLL_S
            if waited < STREAM_TIMEOUT_S:
                if on_wait is not None:
                    on_wait()
                continue
            # produce() queues a sentinel in its finally block no matter what, so
            # a queue still empty after the whole timeout means it never ran at
            # all — a dead event loop after a resource-cache clear, for instance.
            # Without this the page spins forever with no error and no way to
            # recover but a restart.
            future.cancel()
            yield (
                "\n\nThe barista stopped responding — no reply after "
                f"{STREAM_TIMEOUT_S} seconds. Refresh the page and try again."
            )
            return
        # The budget is per chunk, not per reply, so a stream that keeps
        # producing is never cut off.
        waited = 0.0
        if item is _SENTINEL:
            return
        yield item


def brewing_status(container):
    """Put the brewing steps in `container` and return (advance, hide).

    `advance` moves to the next step, `hide` clears the whole thing — called on
    the first real text, since a loader that outlives the answer it was covering
    is just clutter.
    """
    status = container.status(BREWING_STEPS[0])
    step = 0

    def advance() -> None:
        nonlocal step
        step = min(step + 1, len(BREWING_STEPS) - 1)
        status.update(label=BREWING_STEPS[step])

    return advance, container.empty


def as_prices(text: str) -> str:
    """Stop Streamlit reading prices as maths.

    st.markdown treats `$...$` as LaTeX, so a reply naming two prices turns
    everything between them into an italic maths run that also refuses to wrap —
    which is what cut the text off. Escaping the dollars renders them literally.
    Applied at every render site rather than before storing, so what sits in
    session_state stays the barista's actual words.
    """
    return text.replace("$", "\\$")


def keep_raw(chunks, sink):
    """Yield escaped chunks for display while collecting the originals."""
    for chunk in chunks:
        sink.append(chunk)
        yield as_prices(chunk)


def hide_on_first(chunks, hide):
    for i, chunk in enumerate(chunks):
        if i == 0:
            hide()
        yield chunk


# ── State ────────────────────────────────────────────────────────────────────
st.markdown(CSS, unsafe_allow_html=True)
runner = get_runner()

if "session_id" not in st.session_state:
    st.session_state.session_id = f"web-{uuid.uuid4().hex[:12]}"
    st.session_state.user_id = f"user-{uuid.uuid4().hex[:8]}"
    st.session_state.messages = [{"role": "assistant", "content": GREETING}]

# ── Sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.subheader("Today's menu")
    st.caption("Everything we make, all day.")
    menu = _load_menu()
    # Two across, so eight drinks stand a chance of fitting without a scrollbar.
    # No strict= on this zip, unlike the starters below: an odd number of drinks
    # should render short, not raise.
    for start in range(0, len(menu), 2):
        for col, item in zip(st.columns(2), menu[start:start + 2]):
            caffeine = f" · {item['caffeine_mg']} mg" if item.get("caffeine_mg") else ""
            allergens = ", ".join(item["allergens"]) or "no allergens"
            col.markdown(
                f"<div class='drink'>{art_for(item['name'])}"
                f"<div class='drink-name'>{item['name']}</div>"
                f"<div class='drink-price'>${item['price_usd']:.2f}{caffeine}</div>"
                f"<div class='drink-meta'>{allergens}</div></div>",
                unsafe_allow_html=True,
            )

# ── Chat ─────────────────────────────────────────────────────────────────────
st.markdown(
    f"<div class='hdr'>{BARISTA_MARK}<h1>AI Barista</h1></div>",
    unsafe_allow_html=True,
)
st.caption("Ask for a coffee the way you'd ask a person. I only serve what's on the menu.")

for msg in st.session_state.messages:
    avatar = BARISTA_AVATAR if msg["role"] == "assistant" else CUSTOMER_AVATAR
    with st.chat_message(msg["role"], avatar=avatar):
        st.markdown(as_prices(msg["content"]))

# Suggestions, while the conversation hasn't started. A blank input box is the
# hardest thing to answer; these give people somewhere to begin.
starters_shown = len(st.session_state.messages) == 1
if starters_shown:
    st.caption("Or start with one of these:")
    for row in (STARTERS[:2], STARTERS[2:]):
        # strict=True: if the column count and suggestion count ever drift, fail
        # loudly rather than silently dropping a button off the end.
        for col, starter in zip(st.columns(len(row)), row, strict=True):
            if col.button(starter, use_container_width=True, key=f"s-{starter}"):
                st.session_state.pending = starter
                st.rerun()

pending = st.session_state.pop("pending", None)

# st._bottom is the container Streamlit pins to the foot of the page — the one a
# top-level st.chat_input lands in by itself. Declaring both the input and the
# reset button inside it puts them side by side on that pinned row. Plain
# st.columns() looks the same but demotes the input to "inline", so it would
# scroll away with the conversation. Private API, hence the streamlit pin in
# requirements.txt.
with st._bottom, st.container(
    key="inputrow", horizontal=True, vertical_alignment="bottom"
):
    typed = st.chat_input("What are you in the mood for?")
    if st.button(
        "Start over",
        key="startover",
        icon=":material/restart_alt:",
        help="Clear this chat and start again",
    ):
        for key in ("session_id", "user_id", "messages"):
            st.session_state.pop(key, None)
        st.rerun()

prompt = typed or pending

if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user", avatar=CUSTOMER_AVATAR):
        st.markdown(as_prices(prompt))

    with st.chat_message("assistant", avatar=BARISTA_AVATAR):
        advance, hide = brewing_status(st.empty())
        spoken: list[str] = []
        st.write_stream(
            keep_raw(
                hide_on_first(
                    stream_answer(
                        runner,
                        prompt,
                        st.session_state.session_id,
                        st.session_state.user_id,
                        on_wait=advance,
                    ),
                    hide,
                ),
                spoken,
            )
        )
        # Belt and braces: hide_on_first never fires if nothing streamed at all,
        # and a loader stuck above the fallback line looks like a hang.
        hide()
        answer = "".join(spoken).strip() or "Sorry, I didn't catch that — say it again?"

    st.session_state.messages.append({"role": "assistant", "content": answer})
    # Suggestions were drawn higher up this run; rerun so they clear now that
    # the conversation has started.
    if starters_shown:
        st.rerun()
