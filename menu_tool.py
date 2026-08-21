"""Retrieval over the coffee menu — the R in RAG.

Kept free of any ADK or Gemini import so it can be tested offline, with no
API key and no network. agent.py hands this function to the LlmAgent as a tool.
"""

import json
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
        listed = listed.lower()
        for b in blocked:
            # substring both ways so "nut" blocks "nuts" and vice versa
            if b in listed or listed in b:
                return False
    return True


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
    """Search the coffee shop menu and return matching drinks.

    Always call this before recommending anything, and recommend only drinks it
    returns. Never rely on memory for prices, ingredients or allergens.

    Args:
        query: What the customer is after, in their words — a taste, a mood, a
            temperature, a drink name. For example "something strong and iced"
            or "sweet dessert coffee".
        exclude_allergens: Allergens the customer must avoid, such as
            ["dairy", "nuts"]. Pass an empty list if they have stated no
            allergies. Anything containing these is removed before matching.

    Returns:
        A dict with `matches` (drinks safe to offer), `unsafe_matches` (drinks
        the customer asked about that we serve but that contain a blocked
        allergen — mention these honestly, never offer them), and counts.
    """
    menu = _load_menu()
    blocked = {_canonical(a) for a in (exclude_allergens or []) if a and a.strip()}

    words = {w for w in query.lower().replace(",", " ").split() if len(w) > 2}
    safe, unsafe = [], []
    for item in menu:
        (safe if _is_safe(item, blocked) else unsafe).append(item)

    scored = sorted(safe, key=lambda i: _score(i, words), reverse=True)
    matches = [i for i in scored if _score(i, words) > 0][:4]

    # No keyword landed — hand back the whole safe menu rather than nothing, so
    # the agent still has real data to recommend from instead of inventing a drink.
    fell_back = not matches
    if fell_back:
        matches = safe

    # Drinks the customer asked for that we do serve but must not offer them.
    # Without this the agent cannot tell "we don't sell that" from "that would
    # hurt you", and ends up denying the menu item exists.
    unsafe_matches = [
        {"name": i["name"], "allergens": i["allergens"]}
        for i in sorted(unsafe, key=lambda i: _score(i, words), reverse=True)
        if _score(i, words) > 0
    ][:3]

    return {
        "matches": matches,
        "match_count": len(matches),
        "unsafe_matches": unsafe_matches,
        "excluded_allergens": sorted(blocked),
        "items_removed_for_allergens": len(unsafe),
        "no_keyword_match": fell_back,
    }
