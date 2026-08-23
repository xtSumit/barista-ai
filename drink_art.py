"""Ink-line illustrations for the menu, one per drink.

Pure presentation: nothing here is imported by the agent or the retrieval tool,
and the drawings carry no data the model ever sees. Inline SVG rather than image
files, so there is nothing to fetch, nothing to cache and nothing to commit as
a binary — each drawing is a few hundred bytes and inherits the theme's text
colour through `currentColor`.

Deliberately imperfect: rims sit a hair off level and strokes stop just short of
closing, which is the point of the style rather than sloppiness.
"""

_FRAME = (
    '<svg viewBox="0 0 40 40" width="34" height="34" fill="none" '
    'stroke="currentColor" stroke-width="1.3" stroke-linecap="round" '
    'stroke-linejoin="round" aria-hidden="true">{}</svg>'
)

# Keyed on the names in menu.json. A name with no entry falls back to _PLAIN_CUP,
# so adding a drink to the menu can never break the sidebar.
_SHAPES = {
    # demitasse, saucer, two wisps of steam
    "Espresso": (
        '<path d="M12.5 19.5 H29.5 L27.5 30.5 Q27 32.5 25 32.5 H17 '
        'Q15 32.5 14.5 30.5 Z"/>'
        '<path d="M29.5 22 Q33.5 22.5 33 25.5 Q32.5 28 29 27.5"/>'
        '<path d="M10 34.5 H31"/>'
        '<path d="M19 14.5 Q21 12 19.5 9.5"/><path d="M23.5 14 Q25.5 11.5 24 9"/>'
    ),
    # tall glass, low liquid line, two cubes
    "Cold Brew": (
        '<path d="M14 11 H27 L25.5 33 Q25.3 34.5 23.5 34.5 H17.5 '
        'Q15.7 34.5 15.5 33 Z"/>'
        '<path d="M15 17.5 H26.2"/>'
        '<path d="M17.5 20 L20.5 21 L19.5 24 L16.5 23 Z"/>'
        '<path d="M21 25 L24 26 L23 29 L20 28 Z"/>'
    ),
    # wide cup under a dome of foam
    "Cappuccino": (
        '<path d="M11 20 H29 L27 31 Q26.5 33 24.5 33 H15.5 Q13.5 33 13 31 Z"/>'
        '<path d="M11.5 20 Q15 15.5 20 15.8 Q25 16.1 28.5 20"/>'
        '<path d="M29 22.5 Q33.5 23 33 26 Q32.5 28.8 28.8 28.3"/>'
        '<path d="M8.5 35 H32"/>'
    ),
    # handleless tumbler, and an oat sprig for the grain
    "Oat Milk Flat White": (
        '<path d="M14 16 H27 L26 32.5 Q25.8 34 24 34 H17 Q15.2 34 15 32.5 Z"/>'
        '<path d="M15 22 H26"/>'
        '<path d="M31 14 Q33 18.5 32 23"/>'
        '<path d="M31.5 16.2 L33.7 15.2"/><path d="M31.8 19.2 L34 18.4"/>'
    ),
    # tall glass, straw, ice, a line where the milk meets the coffee
    "Iced Vanilla Latte": (
        '<path d="M14 13 H27 L25.5 34 Q25.3 35 23.5 35 H17.5 Q15.7 35 15.5 34 Z"/>'
        '<path d="M22 8.5 L26.5 18"/>'
        '<path d="M15 21.5 H26"/>'
        '<path d="M17 24.5 L20 25.5 L19 28.5 L16 27.5 Z"/>'
    ),
    # mug, steam, and a hazelnut set beside it
    "Hazelnut Mocha": (
        '<path d="M13 16 H26 L25 33 Q24.8 34.5 23 34.5 H16 Q14.2 34.5 14 33 Z"/>'
        '<path d="M26 20 Q30.5 20.5 30 24 Q29.5 27 25.8 26.5"/>'
        '<path d="M18 12 Q20 9.5 18.5 7"/>'
        '<circle cx="32" cy="31.5" r="2.6"/><path d="M29.8 30.6 Q32 33.4 34.2 30.6"/>'
    ),
    # layered glass with caramel drizzled across the top
    "Decaf Soy Caramel Macchiato": (
        '<path d="M14 12.5 H27 L25.5 34 Q25.3 35 23.5 35 H17.5 Q15.7 35 15.5 34 Z"/>'
        '<path d="M15 20 H26.3"/><path d="M15.4 26 H25.8"/>'
        '<path d="M16.5 16 L19 14 L21.5 16 L24 14 L26 15.6"/>'
    ),
    # a scoop in the glass, espresso being poured over it
    "Affogato": (
        '<path d="M14 20 H27 L25.5 31 Q25 33.5 23 33.5 H18 Q16 33.5 15.5 31 Z"/>'
        '<path d="M14.5 20 Q20.5 13 26.5 20"/>'
        '<path d="M23 7.5 Q24.5 11.5 23.5 15.5"/>'
        '<path d="M12 35 H29"/>'
    ),
}

_PLAIN_CUP = (
    '<path d="M13 19 H28 L26.5 31 Q26 33 24 33 H17 Q15 33 14.5 31 Z"/>'
    '<path d="M28 21.5 Q32 22 31.5 25 Q31 27.8 27.5 27.3"/>'
    '<path d="M10.5 35 H30.5"/>'
)


# The barista themselves, for the page heading: cap, shoulders, apron, and a cup
# held out. Drawn at the same weight as the drinks so the two read as one hand.
BARISTA_MARK = _FRAME.replace('width="34" height="34"', 'width="42" height="42"').format(
    '<path d="M13.5 12.5 Q20 8.5 26.5 12.5"/>'
    '<path d="M11.5 12.8 H28.5"/>'
    '<circle cx="20" cy="18.5" r="4.4"/>'
    '<path d="M11 34.5 Q11.6 26 16.2 23.6"/>'
    '<path d="M29 34.5 Q28.4 26 23.8 23.6"/>'
    '<path d="M16.4 26 H23.6 L23 34.5 H17 Z"/>'
    '<path d="M30.5 20.5 H35.5 L34.8 25 Q34.6 26 33.4 26 H32.6 Q31.4 26 31.2 25 Z"/>'
)


def art_for(name: str) -> str:
    """The SVG for a drink, or a plain cup for anything not drawn yet."""
    return _FRAME.format(_SHAPES.get(name, _PLAIN_CUP))
