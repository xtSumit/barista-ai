"""Offline check on the retrieval tool. Run: python test_menu.py

No API key, no network, no framework. Covers the two things that matter:
retrieval returns something relevant, and the allergen filter never leaks.
"""

from menu_tool import _load_menu, search_menu


def test_menu_data_is_well_formed():
    menu = _load_menu()
    assert len(menu) == 8, f"expected 8 drinks, found {len(menu)}"
    for item in menu:
        for field in ("id", "name", "price_usd", "description", "tags", "allergens"):
            assert field in item, f"{item.get('name', item)} is missing {field}"
        assert isinstance(item["tags"], list) and item["tags"]
        assert isinstance(item["allergens"], list)


def test_retrieval_finds_relevant_drinks():
    ids = [i["id"] for i in search_menu("something strong and iced", [])["matches"]]
    assert "cold-brew" in ids, f"cold brew should match strong+iced, got {ids}"

    ids = [i["id"] for i in search_menu("sweet dessert coffee", [])["matches"]]
    assert "affogato" in ids or "hazelnut-mocha" in ids, f"got {ids}"


def test_allergen_filter_never_leaks():
    result = search_menu("creamy sweet latte", ["dairy"])
    for item in result["matches"]:
        assert "dairy" not in [a.lower() for a in item["allergens"]], \
            f"{item['name']} contains dairy but was returned to a dairy-allergic customer"
    assert result["items_removed_for_allergens"] > 0

    # Nut allergy must drop the hazelnut mocha even when the query names it.
    ids = [i["id"] for i in search_menu("hazelnut mocha", ["nuts"])["matches"]]
    assert "hazelnut-mocha" not in ids, f"nut drink leaked to a nut allergy: {ids}"


def test_unsafe_drinks_are_reported_not_hidden():
    # We do serve a hazelnut drink; a nut-allergic customer must be told it
    # exists and is unsafe, not told it doesn't exist.
    result = search_menu("hazelnut mocha", ["nuts"])
    assert "hazelnut-mocha" not in [i["id"] for i in result["matches"]]
    unsafe = [u["name"] for u in result["unsafe_matches"]]
    assert "Hazelnut Mocha" in unsafe, f"unsafe drink was hidden entirely: {unsafe}"
    assert "nuts" in result["unsafe_matches"][0]["allergens"]

    # Nothing unsafe to report when the customer has no allergies.
    assert search_menu("hazelnut mocha", [])["unsafe_matches"] == []


def test_customer_wording_maps_to_menu_allergens():
    # "lactose intolerant" and "peanut" are not menu labels; they must still filter.
    for spoken in (["lactose"], ["milk"], ["cream"]):
        ids = [i["id"] for i in search_menu("latte", spoken)["matches"]]
        assert "cappuccino" not in ids and "iced-vanilla-latte" not in ids, \
            f"{spoken} failed to block dairy drinks: {ids}"

    ids = [i["id"] for i in search_menu("anything", ["peanut"])["matches"]]
    assert "hazelnut-mocha" not in ids, f"'peanut' failed to block nuts: {ids}"

    # Oat milk contains gluten — a coeliac customer must not be offered it.
    ids = [i["id"] for i in search_menu("dairy free milk", ["coeliac"])["matches"]]
    assert "oat-flat-white" not in ids, f"oat drink leaked to a coeliac: {ids}"


def test_unmatched_query_still_returns_safe_options():
    result = search_menu("xyzzy nonsense", ["dairy", "nuts", "soy", "gluten", "egg"])
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
