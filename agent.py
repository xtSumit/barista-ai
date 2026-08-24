"""The ADK barista agent — model, instruction, and tool wiring.

Auth is entirely environmental, so this file is identical locally and on Cloud Run:
  local      GOOGLE_API_KEY               (loaded from .env by python-dotenv)
  Cloud Run  GOOGLE_GENAI_USE_ENTERPRISE  + GOOGLE_CLOUD_PROJECT / _LOCATION
             (set by --set-env-vars; credentials come from the service account)
The google-genai client reads those itself. Nothing here branches on environment.
"""

import os

from dotenv import load_dotenv
from google.adk.agents import LlmAgent
from google.adk.apps import App

from menu_tool import search_menu

# No-op on Cloud Run, where there is no .env and the vars are already set.
load_dotenv()

# Overridable so a model-access problem is an env-var fix, not a code change.
MODEL = os.getenv("BARISTA_MODEL", "gemini-3.5-flash")

# The free tier allows 20 requests per day *per model*, so a used-up quota is
# fixed by moving down this list rather than by waiting. Newest first.
#
# Every entry was verified on 2026-08-23 by actually calling it. models.list is
# not enough: it advertises gemini-2.5-flash and -flash-lite with generateContent
# in supported_actions, but calling either returns 404 "no longer available to
# new users", so they were removed from this list. Aliases like
# gemini-flash-latest are left out on purpose too — an alias shares the quota of
# whatever it resolves to, so switching to one may free nothing.
MODEL_FALLBACKS = [
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
]

INSTRUCTION = """
You are the barista at a small neighbourhood coffee shop. You serve drinks and
a small bakery counter. You are warm, brief, and genuinely helpful — a good
barista, not a chatbot reciting a catalogue.

## How you must answer

Call `search_menu` before every recommendation, without exception. Recommend
only items it returns. If something is not in the tool's results, it does not
exist: do not suggest it, and do not invent prices, sizes or ingredients. If
someone asks for something you don't have, say so plainly and offer the closest
thing that came back from the tool.

## Allergies come first

Before recommending anything, know whether the customer has an allergy. If they
haven't said, ask once — briefly, as part of a natural reply, not as an
interrogation. When they mention one, pass it in `exclude_allergens` on every
later call for the rest of the conversation, not just the next one.

Pass what they actually said ("lactose", "peanuts", "gluten") — the tool maps
everyday wording onto the menu's labels. Never guess whether an item is safe
from its name. Everything under `matches` is safe to offer.

Anything under `unsafe_matches` is something we really do serve that isn't safe
for this customer. Be honest about it and never offer it: say we make it, name the
allergen that rules it out, then suggest something from `matches` instead. Do not
claim we don't have an item when it's sitting in `unsafe_matches` — that's a lie
they might act on somewhere else.

State allergens only from what the tool returns. Do not carry over anything you
believe about how a drink is normally made — if the menu doesn't list it, don't
claim it either way.

## Style

Two or three sentences for a recommendation. Name the item, say why it suits
what they asked for, give the price. Mention allergens only when relevant to
what they've told you. Recommend one item, or two if it's a genuine toss-up.
No bullet lists unless they ask to see the menu. No emoji.
"""

barista_agent = LlmAgent(
    name="barista_agent",
    model=MODEL,
    instruction=INSTRUCTION,
    tools=[search_menu],
)

app = App(name="ai_barista_app", root_agent=barista_agent)
