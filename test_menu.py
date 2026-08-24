"""Offline check on the retrieval tool. Run: python test_menu.py

No API key, no network, no framework. Covers the two things that matter:
retrieval returns something relevant, and the allergen filter never leaks.

Keyed on item names — the codelab menu has no `id` field.
"""

from menu_tool import _load_menu, search_menu


def names(result):
    return [i["name"] for i in result["matches"]]


def test_menu_data_is_well_formed():
    menu = _load_menu()
    assert len(menu) == 8, f"expected 8 items, found {len(menu)}"
    for item in menu:
        for field in ("name", "description", "price", "tags", "allergens"):
            assert field in item, f"{item.get('name', item)} is missing {field}"
        assert isinstance(item["price"], (int, float)) and item["price"] > 0
        assert isinstance(item["tags"], list) and item["tags"]
        assert isinstance(item["allergens"], list)


def test_retrieval_finds_relevant_items():
    got = names(search_menu("something strong and cold", []))
    assert "Cold Brew Coffee" in got or "Nitro Cold Brew" in got, \
        f"a cold brew should match strong+cold, got {got}"

    got = names(search_menu("something sweet and hot", []))
    assert "Oat Milk Honey Latte" in got or "Seasonal Pumpkin Latte" in got, f"got {got}"


def test_allergen_filter_never_leaks():
    result = search_menu("creamy sweet latte", ["dairy"])
    for item in result["matches"]:
        assert "dairy" not in [a.lower() for a in item["allergens"]], \
            f"{item['name']} contains dairy but was returned to a dairy-allergic customer"
    assert result["items_removed_for_allergens"] > 0


def test_wheat_is_blocked_by_the_word_gluten():
    # The menu labels the pastries "wheat"; customers say "gluten" or "coeliac".
    # Both sides are canonicalised, or neither string contains the other and the
    # croissant reads as safe for a coeliac.
    for spoken in ("gluten", "coeliac", "celiac", "wheat"):
        got = names(search_menu("pastry croissant muffin", [spoken]))
        assert "Classic Croissant" not in got and "Vegan Blueberry Muffin" not in got, \
            f"{spoken!r} failed to block the bakery items: {got}"


def test_unsafe_items_are_reported_not_hidden():
    # We do serve a croissant; a coeliac must be told it exists and is unsafe,
    # not told it doesn't exist.
    result = search_menu("classic croissant", ["gluten"])
    assert "Classic Croissant" not in names(result)
    unsafe = [u["name"] for u in result["unsafe_matches"]]
    assert "Classic Croissant" in unsafe, f"unsafe item was hidden entirely: {unsafe}"
    assert "wheat" in result["unsafe_matches"][0]["allergens"]

    # Nothing unsafe to report when the customer has no allergies.
    assert search_menu("classic croissant", [])["unsafe_matches"] == []


def test_punctuation_does_not_defeat_matching():
    # A sentence ends in a full stop. "cold." must still match the tag "cold".
    plain = names(search_menu("something sweet and cold", []))
    punct = names(search_menu("Something sweet and cold.", []))
    assert punct == plain, f"punctuation changed the results: {punct} vs {plain}"

    assert names(search_menu("anything cold?", [])), \
        "trailing question mark returned no matches"


def test_string_instead_of_list_does_not_over_block():
    # Models sometimes pass a bare string. Iterating it yields characters, and
    # single letters substring-match allergen names — the "a" in "dairy" would
    # block "wheat", quietly losing both bakery items.
    as_list = search_menu("sweet", ["dairy"])
    as_string = search_menu("sweet", "dairy")
    assert as_string["excluded_allergens"] == as_list["excluded_allergens"] == ["dairy"]
    assert names(as_string) == names(as_list)
    assert "Vegan Blueberry Muffin" in names(as_string), \
        "a dairy-only exclusion dropped a wheat-only item"


def test_tags_never_contradict_the_allergen_list():
    # "dairy-free" and "vegan" are claims a customer will act on; a tag that
    # disagrees with the allergens field is a lie, not a typo.
    for item in _load_menu():
        listed = {a.lower() for a in item["allergens"]}
        if "dairy-free" in item["tags"] or "vegan" in item["tags"]:
            assert "dairy" not in listed, \
                f"{item['name']} is tagged dairy-free but lists dairy"


def test_customer_wording_maps_to_menu_allergens():
    # "lactose intolerant" and "milk" are not menu labels; they must still filter.
    for spoken in (["lactose"], ["milk"], ["cream"]):
        got = names(search_menu("latte", spoken))
        assert "Seasonal Pumpkin Latte" not in got and "Iced Caramel Macchiato" not in got, \
            f"{spoken} failed to block dairy items: {got}"


def test_unmatched_query_still_returns_safe_options():
    result = search_menu("xyzzy nonsense", ["dairy", "gluten"])
    assert result["no_keyword_match"] is True
    assert result["match_count"] > 0, "should fall back to the safe menu, not nothing"
    for item in result["matches"]:
        assert not item["allergens"], f"{item['name']} should have been filtered out"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"  ok  {name}")
    print("\nall menu checks passed")
