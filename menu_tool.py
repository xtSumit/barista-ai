"""Retrieval over the coffee menu — the R in RAG.

Kept free of any ADK or Gemini import so it can be tested offline, with no
API key and no network. agent.py hands this function to the LlmAgent as a tool.
"""

import json
import re
from pathlib import Path

MENU_PATH = Path(__file__).parent / "menu.json"

# Customers describe allergies in their own words; the menu uses one canonical
# label per allergen. Anything on the left is treated as the allergen on the right.
_ALLERGEN_ALIASES = {
    "milk": "dairy",
    "lactose": "dairy",
    "cream": "dairy",
    "butter": "dairy",
    "cheese": "dairy",
    "nut": "nuts",
    "tree nut": "nuts",
    "tree nuts": "nuts",
    "peanut": "nuts",
    "peanuts": "nuts",
    "almond": "nuts",
    "almonds": "nuts",
    "hazelnut": "nuts",
    "hazelnuts": "nuts",
    "wheat": "gluten",
    "oat": "gluten",
    "oats": "gluten",
    "celiac": "gluten",
    "coeliac": "gluten",
    "soya": "soy",
    "soybean": "soy",
    "eggs": "egg",
}


def _load_menu():
    with MENU_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def _canonical(allergen: str) -> str:
    a = allergen.strip().lower()
    return _ALLERGEN_ALIASES.get(a, a)


def _is_safe(item: dict, blocked: set) -> bool:
    for listed in item["allergens"]:
        # Canonicalised on this side too, not just the customer's words: the menu
        # labels the pastries "wheat" while the alias map turns a customer's
        # "gluten"/"coeliac" into "gluten". Comparing raw labels, neither string
        # contains the other, and the croissant would read as safe for a coeliac.
        listed = _canonical(listed)
        for b in blocked:
            # substring both ways so "nut" blocks "nuts" and vice versa
            if b in listed or listed in b:
                return False
    return True


def _tokenise(query: str) -> set:
    """Words worth matching on, with punctuation stripped.

    Splitting on whitespace alone leaves "cold." and "iced?" — neither matches
    the tag "cold" or "iced", so a perfectly ordinary sentence retrieves nothing.
    """
    cleaned = re.sub(r"[^a-z0-9\s-]", " ", query.lower())
    return {w.strip("-") for w in cleaned.split() if len(w.strip("-")) > 2}


def _score(item: dict, words: set) -> int:
    tags = {t.lower() for t in item["tags"]}
    score = 3 * len(words & tags)
    name = item["name"].lower()
    desc = item["description"].lower()
    for w in words:
        if w in name:
            score += 2
        elif w in desc:
            score += 1
    return score


def search_menu(query: str, exclude_allergens: list[str]) -> dict:
    """Search the coffee shop menu and return matching items.

    Always call this before recommending anything, and recommend only items it
    returns. Never rely on memory for prices, ingredients or allergens.

    Args:
        query: What the customer is after, in their words — a taste, a mood, a
            temperature, an item name. For example "something strong and iced"
            or "sweet dessert coffee".
        exclude_allergens: Allergens the customer must avoid, such as
            ["dairy", "nuts"]. Pass an empty list if they have stated no
            allergies. Anything containing these is removed before matching.

    Returns:
        A dict with `matches` (items safe to offer), `unsafe_matches` (items
        the customer asked about that we serve but that contain a blocked
        allergen — mention these honestly, never offer them), and counts.
    """
    menu = _load_menu()
    if isinstance(exclude_allergens, str):
        # Models occasionally send a bare string. Iterating it would yield single
        # characters, and one-letter substrings match allergen names — "dairy"
        # would become {d,a,i,r,y}, where "y" silently blocks "soy".
        exclude_allergens = [exclude_allergens]
    blocked = {_canonical(a) for a in (exclude_allergens or []) if a and a.strip()}

    words = _tokenise(query)
    safe, unsafe = [], []
    for item in menu:
        (safe if _is_safe(item, blocked) else unsafe).append(item)

    ranked = sorted(((_score(i, words), i) for i in safe), key=lambda t: t[0], reverse=True)
    matches = [i for score, i in ranked if score > 0][:4]

    # No keyword landed — hand back the whole safe menu rather than nothing, so
    # the agent still has real data to recommend from instead of inventing an item.
    fell_back = not matches
    if fell_back:
        matches = safe

    # Items the customer asked for that we do serve but must not offer them.
    # Without this the agent cannot tell "we don't sell that" from "that would
    # hurt you", and ends up denying the menu item exists.
    unsafe_ranked = sorted(
        ((_score(i, words), i) for i in unsafe), key=lambda t: t[0], reverse=True
    )
    unsafe_matches = [
        {"name": i["name"], "allergens": i["allergens"]}
        for score, i in unsafe_ranked
        if score > 0
    ][:3]

    return {
        "matches": matches,
        "match_count": len(matches),
        "unsafe_matches": unsafe_matches,
        "excluded_allergens": sorted(blocked),
        "items_removed_for_allergens": len(unsafe),
        "no_keyword_match": fell_back,
    }
