"""Streamlit chat UI for the ADK barista agent."""

import asyncio
import json
import threading
import uuid

import streamlit as st
from google.adk.runners import InMemoryRunner
from google.genai import types

from agent import MODEL, app as adk_app
from menu_tool import _load_menu

st.set_page_config(page_title="AI Barista", page_icon="☕", layout="centered")

GREETING = (
    "Morning. I'm the barista here — tell me what you're in the mood for and "
    "I'll find you something. Any allergies I should know about?"
)


@st.cache_resource
def get_runner() -> InMemoryRunner:
    """One runner for the process. Holds the in-memory session store, so it must
    survive Streamlit's re-run-the-whole-script-on-every-keystroke model."""
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


def run_sync(coro, timeout: int = 120):
    return asyncio.run_coroutine_threadsafe(coro, get_loop()).result(timeout=timeout)


async def _ensure_session(runner: InMemoryRunner, session_id: str, user_id: str) -> None:
    # auto_create_session defaults to False, so run_async would raise
    # SessionNotFoundError on the first message without this.
    existing = await runner.session_service.get_session(
        app_name=runner.app_name, user_id=user_id, session_id=session_id
    )
    if existing is None:
        await runner.session_service.create_session(
            app_name=runner.app_name, user_id=user_id, session_id=session_id
        )


async def _ask(runner: InMemoryRunner, prompt: str, session_id: str, user_id: str):
    """Send one turn to the agent. Returns (answer_text, drinks_retrieved)."""
    await _ensure_session(runner, session_id, user_id)

    message = types.Content(role="user", parts=[types.Part(text=prompt)])
    answer_parts: list[str] = []
    retrieved: list[str] = []

    async for event in runner.run_async(
        user_id=user_id, session_id=session_id, new_message=message
    ):
        if not (event.content and event.content.parts):
            continue
        for part in event.content.parts:
            # What the RAG tool actually handed back — surfaced in the UI so the
            # grounding is visible rather than a claim.
            fr = getattr(part, "function_response", None)
            if fr is not None:
                payload = fr.response
                if isinstance(payload, str):
                    try:
                        payload = json.loads(payload)
                    except json.JSONDecodeError:
                        payload = {}
                if isinstance(payload, dict):
                    for item in payload.get("matches", []) or []:
                        if isinstance(item, dict) and item.get("name"):
                            retrieved.append(item["name"])
            if event.is_final_response() and getattr(part, "text", None):
                answer_parts.append(part.text)

    answer = "".join(answer_parts).strip()
    return answer, retrieved


# ── State ────────────────────────────────────────────────────────────────────
runner = get_runner()

if "session_id" not in st.session_state:
    st.session_state.session_id = f"web-{uuid.uuid4().hex[:12]}"
    st.session_state.user_id = f"user-{uuid.uuid4().hex[:8]}"
    st.session_state.messages = [{"role": "assistant", "content": GREETING, "retrieved": []}]

# ── Sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.subheader("Today's menu")
    st.caption("The agent's only source of truth — `menu.json`")
    for item in _load_menu():
        allergens = ", ".join(item["allergens"]) or "none"
        st.markdown(f"**{item['name']}** · ${item['price_usd']:.2f}")
        st.caption(f"{', '.join(item['tags'][:4])}  \nAllergens: {allergens}")
    st.divider()
    st.caption(f"Model: `{MODEL}`")
    if st.button("Start a new conversation", use_container_width=True):
        for key in ("session_id", "user_id", "messages"):
            st.session_state.pop(key, None)
        st.rerun()

# ── Chat ─────────────────────────────────────────────────────────────────────
st.title("☕ AI Barista")
st.caption("Grounded in our menu by RAG — it can only recommend what we actually serve.")

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("retrieved"):
            with st.expander(f"Grounded in {len(msg['retrieved'])} menu item(s)"):
                st.write(", ".join(dict.fromkeys(msg["retrieved"])))

if prompt := st.chat_input("What are you in the mood for?"):
    st.session_state.messages.append({"role": "user", "content": prompt, "retrieved": []})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Checking the menu..."):
            try:
                answer, retrieved = run_sync(
                    _ask(runner, prompt, st.session_state.session_id, st.session_state.user_id)
                )
            except Exception as exc:
                text = str(exc)
                retrieved = []
                if "RESOURCE_EXHAUSTED" in text or "429" in text or "UNAVAILABLE" in text:
                    # The free tier caps requests per DAY per model, so "wait and
                    # retry" is the wrong advice — switching model is what works.
                    daily = "PerDay" in text or "per day" in text.lower()
                    detail = (
                        "is used up for today. The free tier allows 20 requests per day "
                        "per model, and each chat turn costs about two."
                        if daily else
                        "was hit. This one is usually a short burst limit — try again shortly."
                    )
                    answer = (
                        f"Sorry, the coffee machine is backed up. Gemini quota on "
                        f"`{MODEL}` {detail}\n\nTo switch to a model with its own quota, "
                        "stop the app and restart it with:\n\n"
                        '`$env:BARISTA_MODEL="gemini-3.7-flash"`\n\n`streamlit run app.py`'
                    )
                else:
                    # Anything else is a real bug — show it rather than hide it.
                    answer = f"Something went wrong talking to Gemini:\n\n`{exc}`"
        answer = answer or "Sorry, I didn't catch that — say it again?"
        st.markdown(answer)
        if retrieved:
            with st.expander(f"Grounded in {len(retrieved)} menu item(s)"):
                st.write(", ".join(dict.fromkeys(retrieved)))

    st.session_state.messages.append(
        {"role": "assistant", "content": answer, "retrieved": retrieved}
    )
