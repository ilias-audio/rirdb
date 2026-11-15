"""What a licence lets you do with the audio: one table, one function, used everywhere.

The table is `licences.csv`, next to this file: one row per licence id, and four
answers per row.

    research      may I use the audio in research?             yes | no | unknown
    commercial    ... in a commercial product or service?      yes | no | unknown
    training      ... to train a machine-learning model?       yes | no | unknown
    redistribute  may I pass the audio on to others?           yes | unaltered_only | no | unknown

A "yes" can come with conditions: attribution, share_alike, non_commercial,
no_derivatives. For example CC BY-NC allows training, but only non-commercially:
`training == "yes"` and `commercial == "no"`.

"unknown" means the licence does not say, or no licence was found. Treat it as
"read the terms or ask the authors", never as "yes".

To add a licence, add a row to `licences.csv`. A licence id that is not in the
table is an error, so a new id cannot be read by accident as a similar one.
This is a reading aid, not legal advice.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import cache
from pathlib import Path

YES, NO, UNKNOWN, UNALTERED_ONLY = "yes", "no", "unknown", "unaltered_only"
ANSWERS = (YES, NO, UNKNOWN)
REDISTRIBUTE_ANSWERS = (YES, UNALTERED_ONLY, NO, UNKNOWN)
CONDITIONS = ("attribution", "share_alike", "non_commercial", "no_derivatives")
TABLE = Path(__file__).with_name("licences.csv")


class UnknownLicence(KeyError):
    """The licence id has no row in licences.csv."""


@dataclass(frozen=True)
class Uses:
    research: str
    commercial: str
    training: str
    redistribute: str
    conditions: tuple[str, ...] = ()

    @property
    def non_commercial(self) -> bool:
        return "non_commercial" in self.conditions

    @property
    def share_alike(self) -> bool:
        return "share_alike" in self.conditions

    @property
    def no_derivatives(self) -> bool:
        return "no_derivatives" in self.conditions


@cache
def table() -> dict[str, Uses]:
    """licences.csv as {licence id: Uses}. Every cell is checked when the table is read."""
    out: dict[str, Uses] = {}
    with open(TABLE, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            conditions = tuple(row["conditions"].split())
            bad = [c for c in conditions if c not in CONDITIONS]
            answers_ok = all(row[k] in ANSWERS for k in ("research", "commercial", "training"))
            if bad or not answers_ok or row["redistribute"] not in REDISTRIBUTE_ANSWERS or row["id"] in out:
                raise ValueError(f"licences.csv: bad or repeated row for {row['id']!r}")
            out[row["id"]] = Uses(row["research"], row["commercial"], row["training"], row["redistribute"], conditions)
    return out


def permitted(spdx: str, *, research: str | None = None, commercial: str | None = None,
              training: str | None = None, redistribute: str | None = None) -> Uses:
    """Read a licence. Keyword arguments are explicit answers from the registry; they win when given.

    `spdx` is an SPDX id (`CC-BY-NC-SA-4.0`) or a `LicenseRef-...` id for custom terms.
    Where statements conflict, pass the strictest one.
    """
    try:
        base = table()[spdx]
    except KeyError:
        raise UnknownLicence(f"licence id {spdx!r} has no row in {TABLE.name}: add one") from None

    def pick(explicit: str | None, from_table: str, allowed: tuple[str, ...]) -> str:
        if explicit is None or explicit == UNKNOWN:      # "unknown" is not an answer: keep the table's
            return from_table
        if explicit not in allowed:
            raise ValueError(f"licence answer must be one of {allowed}, got {explicit!r}")
        return explicit

    return Uses(
        research=pick(research, base.research, ANSWERS),
        commercial=pick(commercial, base.commercial, ANSWERS),
        training=pick(training, base.training, ANSWERS),
        redistribute=pick(redistribute, base.redistribute, REDISTRIBUTE_ANSWERS),
        conditions=base.conditions,
    )


_OPENNESS = {YES: 3, UNALTERED_ONLY: 2, UNKNOWN: 1, NO: 0}


def strictest(ids: list[str]) -> str:
    """The licence id that allows least, for statements that conflict.

    Fewest uses allowed first (yes > unaltered_only > unknown > no, over the four answers), then
    the most conditions. A statement that names no licence (LicenseRef-unspecified) is stricter
    than any open licence, since nothing may be assumed.
    """
    def openness(i: str) -> tuple[int, int]:
        u = permitted(i)
        return (sum(_OPENNESS[a] for a in (u.research, u.commercial, u.training, u.redistribute)), -len(u.conditions))

    return min(ids, key=openness)
