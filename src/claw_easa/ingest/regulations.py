"""Which regulations a source document is built from.

One Easy Access Rules document can consolidate more than one regulation.  The
Information Security EAR publishes two: Implementing Regulation (EU) 2023/203
and Delegated Regulation (EU) 2022/1645.  They cover different organisations,
are amended on their own timelines, and each states its own annexes — so a
citation has to name the one that actually states the rule.

The annex label cannot recover that distinction after the fact.  The
implementing regulation numbers its annexes ``ANNEX I`` and ``ANNEX II``; the
delegated regulation states a single annex and does not number it at all.  A
label is therefore neither unique across the document nor always present.  The
attribution is declared here instead, against the part code EASA gives each
annex, rather than guessed at parse time from the position of a heading.

The declaration is opt-in.  A source that is not listed contributes no
regulation provenance and is not held to any — ``regulations_for`` answers
with an empty tuple and every part of it stays unattributed without
complaint.  A source that *is* listed is held to its declaration: a part code
no declared regulation claims stays unattributed and is reported, because
filing it under either regulation would attribute requirements to a
regulation that does not state them.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

#: A regulation that lays down implementing rules (Commission Implementing
#: Regulation) as opposed to one adopted under a delegation of power
#: (Commission Delegated Regulation).  The two are distinct legal acts and
#: are cited as such.
IMPLEMENTING = "implementing"
DELEGATED = "delegated"


@dataclass(frozen=True)
class Regulation:
    """A regulation contributing part of a source document."""
    identifier: str
    title: str
    kind: str
    #: Part codes stated by this regulation, as EASA spells them in the
    #: ``ANNEX x (Part-yyy)`` headings of the consolidated document.
    part_codes: tuple[str, ...]

    def claims(self, part_code: str) -> bool:
        return part_code.upper() in {code.upper() for code in self.part_codes}


#: Regulation (EU) 2023/203 — information security management for the
#: organisations and competent authorities covered by the implementing acts.
#: ANNEX I [PART-IS.AR] states the competent authority requirements, ANNEX II
#: [PART-IS.I.OR] the organisation requirements.
INFORMATION_SECURITY_IMPLEMENTING = Regulation(
    identifier="(EU) 2023/203",
    title="Commission Implementing Regulation (EU) 2023/203",
    kind=IMPLEMENTING,
    part_codes=("IS.I.OR", "IS.AR"),
)

#: Regulation (EU) 2022/1645 — information security management for the
#: organisations covered by the delegated acts.  It states one annex, headed
#: 'ANNEX ... [PART-IS.D.OR]' with no numeral, holding its organisation
#: requirements.
INFORMATION_SECURITY_DELEGATED = Regulation(
    identifier="(EU) 2022/1645",
    title="Commission Delegated Regulation (EU) 2022/1645",
    kind=DELEGATED,
    part_codes=("IS.D.OR",),
)

#: Sources whose provenance is more than one regulation, or whose regulation
#: is worth recording explicitly.  Order is the order EASA lists them in the
#: document title.
SOURCE_REGULATIONS: dict[str, tuple[Regulation, ...]] = {
    "information-security": (
        INFORMATION_SECURITY_IMPLEMENTING,
        INFORMATION_SECURITY_DELEGATED,
    ),
}


def regulations_for(slug: str) -> tuple[Regulation, ...]:
    """The regulations *slug* is declared to be built from, if any."""
    return SOURCE_REGULATIONS.get(slug, ())


def attribute_part(
    regulations: Sequence[Regulation], part_code: str,
) -> str | None:
    """The identifier of the regulation in *regulations* stating *part_code*.

    ``None`` means either that *regulations* is empty — the source declares
    none — or that no declared regulation claims this part.  The caller has
    to tell those apart, and must not file an unclaimed part under a
    regulation by proximity.
    """
    for regulation in regulations:
        if regulation.claims(part_code):
            return regulation.identifier
    return None
