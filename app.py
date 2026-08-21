"""Streamlit chat UI for the ADK barista agent."""

import asyncio
import json
import queue
import threading
import uuid

import streamlit as st
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.runners import InMemoryRunner
from google.genai import types

from agent import MODEL, app as adk_app
from menu_tool import _load_menu

st.set_page_config(page_title="AI Barista", page_icon="☕", layout="centered")

BARISTA_AVATAR = "☕"
CUSTOMER_AVATAR = "🙂"

GREETING = (
    "Morning. I'm the barista here — tell me what you're in the mood for and "
    "I'll find you something. Any allergies I should know about?"
)

STARTERS = [
    "Something strong and iced",
    "I'm lactose intolerant",
    "Something sweet and cold",
    "Surprise me",
]

# Streamlit owns the layout; this only softens what it already draws. Kept small
# on purpose — deep selectors into Streamlit's DOM break on version upgrades.
CSS = """
<style>
  .stChatMessage { border-radius: 14px; padding: 0.6rem 0.9rem; }
  .stChatInput textarea { border-radius: 12px; }
  div[data-testid="stChatMessageAvatarUser"],
  div[data-testid="stChatMessageAvatarAssistant"] { font-size: 1.15rem; }
  section[data-testid="stSidebar"] h3 { margin-bottom: 0.1rem; }
  .drink-name { font-weight: 600; }
  .drink-meta { font-size: 0.78rem; opacity: 0.75; line-height: 1.45; }
</style>
"""

_SENTINEL = object()

# Longest wait for the *next* chunk, not for the whole reply — so a slow but
# healthy stream is never cut off, while a dead one fails visibly.
STREAM_TIMEOUT_S = 90


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
            f"{detail}\n\nTo switch to a model with its own quota, stop the app and "
            'restart with:\n\n`$env:BARISTA_MODEL="gemini-3.7-flash"`'
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


def _collect_retrieved(part, retrieved: list) -> None:
    """Record the drink names the RAG tool handed back, so the UI can show them."""
    fr = getattr(part, "function_response", None)
    if fr is None:
        return
    payload = fr.response
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return
    if isinstance(payload, dict):
        for item in payload.get("matches", []) or []:
            if isinstance(item, dict) and item.get("name"):
                retrieved.append(item["name"])


def stream_answer(runner, prompt: str, session_id: str, user_id: str, retrieved: list):
    """Yield the barista's reply in chunks, for st.write_stream.

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
                    _collect_retrieved(part, retrieved)
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

    while True:
        try:
            item = chunks.get(timeout=STREAM_TIMEOUT_S)
        except queue.Empty:
            # produce() queues a sentinel in its finally block no matter what, so
            # an empty queue means it never ran at all — a dead event loop after a
            # resource-cache clear, for instance. Without this the page spins
            # forever with no error and no way to recover but a restart.
            future.cancel()
            yield (
                "\n\nThe barista stopped responding — no reply after "
                f"{STREAM_TIMEOUT_S} seconds. Refresh the page and try again."
            )
            return
        if item is _SENTINEL:
            return
        yield item


def grounding_note(retrieved: list) -> None:
    if not retrieved:
        return
    unique = list(dict.fromkeys(retrieved))
    with st.expander(f"Grounded in {len(unique)} menu item(s)"):
        st.write(", ".join(unique))


# ── State ────────────────────────────────────────────────────────────────────
st.markdown(CSS, unsafe_allow_html=True)
runner = get_runner()

if "session_id" not in st.session_state:
    st.session_state.session_id = f"web-{uuid.uuid4().hex[:12]}"
    st.session_state.user_id = f"user-{uuid.uuid4().hex[:8]}"
    st.session_state.messages = [
        {"role": "assistant", "content": GREETING, "retrieved": []}
    ]

# ── Sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.subheader("Today's menu")
    st.caption("Everything we make, all day.")
    for item in _load_menu():
        with st.container(border=True):
            st.markdown(
                f"<span class='drink-name'>{item['name']}</span> · "
                f"${item['price_usd']:.2f}<br>"
                f"<span class='drink-meta'>{', '.join(item['tags'][:4])}<br>"
                f"Allergens: {', '.join(item['allergens']) or 'none'}"
                + (f" · {item['caffeine_mg']} mg caffeine" if item.get('caffeine_mg') else "")
                + "</span>",
                unsafe_allow_html=True,
            )
    with st.expander("About this barista"):
        st.caption(
            "Recommendations come only from the menu above — never from memory. "
            "Anything you mention an allergy to is filtered out before a "
            "suggestion is made."
        )
        st.caption(f"Running on Google's ADK with `{MODEL}`.")
    if st.button("Start a new conversation", use_container_width=True):
        for key in ("session_id", "user_id", "messages"):
            st.session_state.pop(key, None)
        st.rerun()

# ── Chat ─────────────────────────────────────────────────────────────────────
st.title("☕ AI Barista")
st.caption("Ask for a coffee the way you'd ask a person. I only serve what's on the menu.")

for msg in st.session_state.messages:
    avatar = BARISTA_AVATAR if msg["role"] == "assistant" else CUSTOMER_AVATAR
    with st.chat_message(msg["role"], avatar=avatar):
        st.markdown(msg["content"])
        grounding_note(msg.get("retrieved", []))

# Suggestions, while the conversation hasn't started. A blank input box is the
# hardest thing to answer; these give people somewhere to begin.
starters_shown = len(st.session_state.messages) == 1
if starters_shown:
    st.caption("Or start with one of these:")
    for row in (STARTERS[:2], STARTERS[2:]):
        for col, starter in zip(st.columns(len(row)), row):
            if col.button(starter, use_container_width=True, key=f"s-{starter}"):
                st.session_state.pending = starter
                st.rerun()

pending = st.session_state.pop("pending", None)
prompt = st.chat_input("What are you in the mood for?") or pending

if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt, "retrieved": []})
    with st.chat_message("user", avatar=CUSTOMER_AVATAR):
        st.markdown(prompt)

    with st.chat_message("assistant", avatar=BARISTA_AVATAR):
        retrieved: list = []
        answer = st.write_stream(
            stream_answer(
                runner,
                prompt,
                st.session_state.session_id,
                st.session_state.user_id,
                retrieved,
            )
        )
        answer = (answer or "").strip() or "Sorry, I didn't catch that — say it again?"
        grounding_note(retrieved)

    st.session_state.messages.append(
        {"role": "assistant", "content": answer, "retrieved": retrieved}
    )
    # Suggestions were drawn higher up this run; rerun so they clear now that
    # the conversation has started.
    if starters_shown:
        st.rerun()
