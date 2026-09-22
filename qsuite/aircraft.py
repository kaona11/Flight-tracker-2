"""Qsuite inference from aircraft type.

Award search engines rarely say "Qsuite" -- they give you an equipment code at
best. Qatar's business-class hard product splits by fleet, so equipment is the
only signal most engines expose. This is a *heuristic*: sub-fleets vary,
retrofits are ongoing, and swaps happen after booking. Treat ``True`` as
"very likely" and always re-check the seat map before ticketing.
"""

from __future__ import annotations

from typing import Optional

# Equipment families that carry Qsuite (1-2-1 with doors).
QSUITE_FLEET = {
    "77W": "777-300ER: Qsuite across the sub-fleet",
    "773": "777-300ER: Qsuite across the sub-fleet",
    "35K": "A350-1000: Qsuite from delivery",
    "351": "A350-1000: Qsuite from delivery",
    "359": "A350-900: Qsuite on most frames (some early frames still 1-2-1 legacy)",
    "35X": "A350: Qsuite on most frames",
    "781": "787-9: Qsuite on the newer 787-9 frames",
}

# Equipment families that do NOT carry Qsuite (reverse-herringbone or regional).
NON_QSUITE_FLEET = {
    "788": "787-8: legacy 1-2-1 reverse herringbone, no doors",
    "789": "787-9: mixed -- older frames are legacy business",
    "77L": "777-200LR: legacy business",
    "772": "777-200: legacy business",
    "320": "A320: regional recliner/flat, not Qsuite",
    "321": "A321: regional, not Qsuite",
    "32N": "A321neo: Qsuite Next Gen on some frames, not classic Qsuite",
    "319": "A319: regional",
    "388": "A380: legacy first/business, no Qsuite",
}

# Normalising map for the many ways engines spell equipment.
_ALIASES = {
    "BOEING 777-300ER": "77W",
    "B777-300ER": "77W",
    "777-300ER": "77W",
    "777300ER": "77W",
    "B77W": "77W",
    "AIRBUS A350-1000": "35K",
    "A350-1000": "35K",
    "A3501000": "35K",
    "A350-900": "359",
    "AIRBUS A350-900": "359",
    "BOEING 787-9": "789",
    "787-9": "789",
    "BOEING 787-8": "788",
    "787-8": "788",
    "AIRBUS A380-800": "388",
    "A380-800": "388",
}


def normalise(equipment: str) -> str:
    """Fold a free-text equipment string down to a 3-char IATA-ish code."""
    if not equipment:
        return ""
    raw = equipment.strip().upper()
    if raw in _ALIASES:
        return _ALIASES[raw]
    compact = raw.replace(" ", "").replace("_", "")
    if compact in _ALIASES:
        return _ALIASES[compact]
    # Already a short code?
    if 3 <= len(compact) <= 4:
        return compact[:3]
    # Try to spot a family inside a longer marketing string.
    for key, code in _ALIASES.items():
        if key.replace(" ", "") in compact:
            return code
    return compact[:3]


def is_qsuite(equipment: Optional[str]) -> tuple[Optional[bool], str]:
    """Return ``(verdict, reason)``.

    ``verdict`` is ``True``/``False``/``None`` where ``None`` means the engine
    did not give us enough to decide.
    """
    if not equipment:
        return None, "no equipment reported by this engine"
    code = normalise(equipment)
    if code in QSUITE_FLEET:
        return True, QSUITE_FLEET[code]
    if code in NON_QSUITE_FLEET:
        return False, NON_QSUITE_FLEET[code]
    return None, f"unrecognised equipment {equipment!r} (normalised {code!r})"


def verdict_for_itinerary(equipment_list: list[str]) -> tuple[Optional[bool], str]:
    """Qsuite verdict for a multi-leg itinerary.

    An itinerary is only "Qsuite" if *every* long-haul leg we can identify is a
    Qsuite frame. A single legacy leg downgrades the whole trip, because that is
    how it feels to the person in the seat.
    """
    if not equipment_list:
        return None, "no equipment reported by this engine"
    verdicts = [is_qsuite(e) for e in equipment_list]
    if any(v is False for v, _ in verdicts):
        bad = [r for v, r in verdicts if v is False]
        return False, "at least one leg is not Qsuite: " + "; ".join(bad)
    if all(v is True for v, _ in verdicts):
        return True, "; ".join(r for _, r in verdicts)
    return None, "; ".join(r for _, r in verdicts)
