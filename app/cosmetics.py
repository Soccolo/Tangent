"""Server-owned cosmetic catalogue and public appearance serializers.

Cosmetic keys are deliberately opaque identifiers for artwork bundled with the
client.  Neither the workshop nor a public profile ever accepts markup, CSS or
URLs from a user.
"""

from __future__ import annotations

from .models import User


WORKSHOP_CATALOG = (
    {
        "key": "owl_star_pin",
        "name": "North Star pin",
        "description": "A small golden star for curious explorers.",
        "slot": "owl_accessory",
        "price": 25,
        "preview": "⭐",
    },
    {
        "key": "owl_scholar_cap",
        "name": "Scholar's cap",
        "description": "A small violet cap for Tangent.",
        "slot": "owl_accessory",
        "price": 45,
        "preview": "🎓",
    },
    {
        "key": "owl_scarf",
        "name": "Explorer scarf",
        "description": "A bright scarf for taking ideas somewhere new.",
        "slot": "owl_accessory",
        "price": 55,
        "preview": "🧣",
    },
    {
        "key": "owl_bow",
        "name": "Teal bow",
        "description": "A neat teal bow for a little extra character.",
        "slot": "owl_accessory",
        "price": 60,
        "preview": "🎀",
    },
    {
        "key": "owl_star_glasses",
        "name": "Star glasses",
        "description": "A brighter look for ambitious tangents.",
        "slot": "owl_accessory",
        "price": 70,
        "preview": "🤩",
    },
    {
        "key": "desk_fern",
        "name": "Desk fern",
        "description": "Something green beside the lesson cards.",
        "slot": "desk_item",
        "price": 55,
        "preview": "🌿",
    },
    {
        "key": "cards_aurora",
        "name": "Aurora cards",
        "description": "A soft northern-light treatment for concept cards.",
        "slot": "card_theme",
        "price": 80,
        "preview": "aurora",
    },
    {
        "key": "celebration_stars",
        "name": "Star shower",
        "description": "Trade confetti for a shower of tiny stars.",
        "slot": "celebration",
        "price": 65,
        "preview": "✨",
    },
)

WORKSHOP_BY_KEY = {item["key"]: item for item in WORKSHOP_CATALOG}

EQUIPPED_FIELDS = {
    "owl_accessory": "equipped_owl_accessory",
    "desk_item": "equipped_desk_item",
    "card_theme": "equipped_card_theme",
    "celebration": "equipped_celebration",
}


def equipped_cosmetics(user: User) -> dict[str, str | None]:
    """Return only catalogue keys valid for the slot in which they are stored.

    Equip endpoints already enforce this boundary.  Rechecking during
    serialization also makes legacy or manually-corrupted rows harmless.
    """

    equipped: dict[str, str | None] = {}
    for slot, field in EQUIPPED_FIELDS.items():
        key = getattr(user, field, None)
        definition = WORKSHOP_BY_KEY.get(key) if key else None
        equipped[slot] = key if definition and definition["slot"] == slot else None
    return equipped


def owl_profile_picture(user: User) -> dict:
    """A small, safe public descriptor that clients render with bundled SVG."""

    return {
        "kind": "owl",
        "owl": {
            "accessory": equipped_cosmetics(user)["owl_accessory"],
        },
    }
