"""Closed-vocabulary correction for OCR-noised field values.

The challenge's field values come from small closed sets (species codes, home
worlds, visa classes, declared purposes). OCR introduces character-level noise
("ANOROMEDAN", "Wolf-1061¢"). Correcting against the vocabulary converts those
near-misses into exact matches, and also repairs downstream policy checks
(e.g. embargo-world detection for "Wolf-1061¢").

Correction is conservative: a value is only replaced when it is unambiguously
close to exactly one vocabulary entry; unknown values pass through unchanged, so
new categories in unseen data are preserved rather than force-mapped.

The vocabularies are general policy facts observed across the public training
data (the same closed sets appear on every packet type), not per-case answers.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

SPECIES = [
    "ALPHA_DRACONIAN", "ANDROMEDAN", "AQUARIAN_MANTIS", "ARCTURIAN",
    "CENTAURI_SYNTH", "JOVIAN_GASFORM", "KAIJU_MICRO", "LUNA_SECURID",
    "ORION_GRAYS", "SIRIUS_AVIAN", "TRIANGULAN", "VENUSIAN_MYCELIAL",
]
HOME_WORLDS = [
    "Barnard-c", "Eris Relay", "Europa Station", "Gliese-581g", "Kepler-186f",
    "Luyten-b", "Mars Dome-7", "Proxima-b", "Sirius Outpost", "TRAPPIST-1e",
    "Titan Freeport", "Wolf-1061c", "Zeta Reticuli",
]
VISA_CLASSES = ["XW-1", "XW-2", "DIP-1", "MED-3", "TRANSIT-7"]
PURPOSES = [
    "archive audit", "cultural exchange", "diplomatic", "field repair",
    "medical consult", "reactor maintenance", "research", "transit",
    "translation", "xenobotany",
]
FEE_VALUES = ["paid", "waived", "unpaid", "unknown"]


def _canon(s: str) -> str:
    """Case/punctuation-insensitive canonical form for comparison."""
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _edit_distance(a: str, b: str, cap: int = 4) -> int:
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) > cap:
            return cap + 1
        prev = cur
    return prev[-1]


_FOLD = str.maketrans({"0": "o", "1": "l", "5": "s", "8": "b", "q": "o"})


def _fold(s: str) -> str:
    """Collapse classic OCR confusions (rn~m, 0~o, 1~l...) for comparison."""
    return s.replace("rn", "m").translate(_FOLD)


def correct(value: str, vocabulary: Iterable[str], max_ratio: float = 0.34) -> Optional[str]:
    """Return the vocabulary entry ``value`` most plausibly is, or None.

    A match requires edit distance (on canonical forms) within ``max_ratio`` of
    the entry length and a clear winner over the runner-up, so ambiguous noise
    is never force-assigned.
    """
    v = _canon(value or "")
    if not v:
        return None
    scored = []
    for entry in vocabulary:
        e = _canon(entry)
        cap = max(1, int(len(e) * max_ratio))
        d = min(_edit_distance(v, e, cap=cap + 1),
                _edit_distance(_fold(v), _fold(e), cap=cap + 1))
        if d <= cap:
            scored.append((d, entry))
    if not scored:
        return None
    scored.sort(key=lambda x: x[0])
    if len(scored) > 1 and scored[1][0] - scored[0][0] < 1:
        return None  # ambiguous between two entries
    return scored[0][1]


# Worlds under embargo: fuzzy-mapping a NEW world name onto one of these flips
# an innocent applicant to DENIED, so mapping onto them demands near-certainty.
_EMBARGO_CANON = {"wolf1061c", "trappist1e", "erisrelay"}


def correct_field(field: str, value: str, strict: bool = False) -> str:
    """Correct ``value`` for ``field`` against its vocabulary; passthrough if no
    confident match.

    ``strict=True`` (digital text, which is near-exact) allows exact-canonical
    matches only: a cleanly-rendered unfamiliar value is a NEW value, not noise,
    and must never be force-mapped onto the training vocabulary.
    """
    vocab = {
        "species_code": SPECIES,
        "home_world": HOME_WORLDS,
        "visa_class": VISA_CLASSES,
        "declared_purpose": PURPOSES,
        "fee_status": FEE_VALUES,
    }.get(field)
    if not vocab or not value:
        return value
    exact = {_canon(e): e for e in vocab}
    hit = exact.get(_canon(value))
    if hit:
        return hit
    if strict:
        return value
    fixed = correct(value, vocab)
    if not fixed:
        return value
    # A near-miss that lands on an embargo world must be a 1-edit misread
    # (e.g. 'Wolf-1061c' with a broken glyph), never a neighboring designator
    # like 'Wolf-1062d' — that's a different world.
    if field == "home_world" and _canon(fixed) in _EMBARGO_CANON:
        if _edit_distance(_fold(_canon(value)), _fold(_canon(fixed)), cap=2) > 1:
            return value
    return fixed

# Applicant-name token vocabularies, derived from the training labels (see
# scripts/derive_policy_lists.py): the generator draws every name from a closed
# set of 144 first / 144 last tokens, and 365 cleanly-typed validation
# packets contain zero tokens outside this set. Generator vocabulary learned
# from labeled examples, not per-case answers.
NAME_FIRST = [
    "Aridane", "Ariix", "Arikesh", "Arimora", "Arinax", "Ariquell",
    "Aririx", "Aritari", "Ariul", "Arivara", "Arivoss", "Arizarn",
    "Ixodane", "Ixoix", "Ixokesh", "Ixomora", "Ixonax", "Ixoquell",
    "Ixorix", "Ixotari", "Ixoul", "Ixovara", "Ixovoss", "Ixozarn",
    "Ludane", "Luix", "Lukesh", "Lumora", "Lunax", "Luquell",
    "Lurix", "Lutari", "Luul", "Luvara", "Luvoss", "Luzarn",
    "Miradane", "Miraix", "Mirakesh", "Miramora", "Miranax", "Miraquell",
    "Mirarix", "Miratari", "Miraul", "Miravara", "Miravoss", "Mirazarn",
    "Nexdane", "Nexix", "Nexkesh", "Nexmora", "Nexnax", "Nexquell",
    "Nexrix", "Nextari", "Nexul", "Nexvara", "Nexvoss", "Nexzarn",
    "Oridane", "Oriix", "Orikesh", "Orimora", "Orinax", "Oriquell",
    "Oririx", "Oritari", "Oriul", "Orivara", "Orivoss", "Orizarn",
    "Qordane", "Qorix", "Qorkesh", "Qormora", "Qornax", "Qorquell",
    "Qorrix", "Qortari", "Qorul", "Qorvara", "Qorvoss", "Qorzarn",
    "Soldane", "Solix", "Solkesh", "Solmora", "Solnax", "Solquell",
    "Solrix", "Soltari", "Solul", "Solvara", "Solvoss", "Solzarn",
    "Tekdane", "Tekix", "Tekkesh", "Tekmora", "Teknax", "Tekquell",
    "Tekrix", "Tektari", "Tekul", "Tekvara", "Tekvoss", "Tekzarn",
    "Veedane", "Veeix", "Veekesh", "Veemora", "Veenax", "Veequell",
    "Veerix", "Veetari", "Veeul", "Veevara", "Veevoss", "Veezarn",
    "Xandane", "Xanix", "Xankesh", "Xanmora", "Xannax", "Xanquell",
    "Xanrix", "Xantari", "Xanul", "Xanvara", "Xanvoss", "Xanzarn",
    "Zadane", "Zaix", "Zakesh", "Zamora", "Zanax", "Zaquell",
    "Zarix", "Zatari", "Zaul", "Zavara", "Zavoss", "Zazarn",
]

NAME_LAST = [
    "Aridane", "Ariix", "Arikesh", "Arimora", "Arinax", "Ariquell",
    "Aririx", "Aritari", "Ariul", "Arivara", "Arivoss", "Arizarn",
    "Ixodane", "Ixoix", "Ixokesh", "Ixomora", "Ixonax", "Ixoquell",
    "Ixorix", "Ixotari", "Ixoul", "Ixovara", "Ixovoss", "Ixozarn",
    "Ludane", "Luix", "Lukesh", "Lumora", "Lunax", "Luquell",
    "Lurix", "Lutari", "Luul", "Luvara", "Luvoss", "Luzarn",
    "Miradane", "Miraix", "Mirakesh", "Miramora", "Miranax", "Miraquell",
    "Mirarix", "Miratari", "Miraul", "Miravara", "Miravoss", "Mirazarn",
    "Nexdane", "Nexix", "Nexkesh", "Nexmora", "Nexnax", "Nexquell",
    "Nexrix", "Nextari", "Nexul", "Nexvara", "Nexvoss", "Nexzarn",
    "Oridane", "Oriix", "Orikesh", "Orimora", "Orinax", "Oriquell",
    "Oririx", "Oritari", "Oriul", "Orivara", "Orivoss", "Orizarn",
    "Qordane", "Qorix", "Qorkesh", "Qormora", "Qornax", "Qorquell",
    "Qorrix", "Qortari", "Qorul", "Qorvara", "Qorvoss", "Qorzarn",
    "Soldane", "Solix", "Solkesh", "Solmora", "Solnax", "Solquell",
    "Solrix", "Soltari", "Solul", "Solvara", "Solvoss", "Solzarn",
    "Tekdane", "Tekix", "Tekkesh", "Tekmora", "Teknax", "Tekquell",
    "Tekrix", "Tektari", "Tekul", "Tekvara", "Tekvoss", "Tekzarn",
    "Veedane", "Veeix", "Veekesh", "Veemora", "Veenax", "Veequell",
    "Veerix", "Veetari", "Veeul", "Veevara", "Veevoss", "Veezarn",
    "Xandane", "Xanix", "Xankesh", "Xanmora", "Xannax", "Xanquell",
    "Xanrix", "Xantari", "Xanul", "Xanvara", "Xanvoss", "Xanzarn",
    "Zadane", "Zaix", "Zakesh", "Zamora", "Zanax", "Zaquell",
    "Zarix", "Zatari", "Zaul", "Zavara", "Zavoss", "Zazarn",
]
