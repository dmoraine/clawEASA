from __future__ import annotations

from dataclasses import dataclass
import re


EASA_TRIGGER_PATTERNS = [
    r'\bEASA\b',
    r'\bAMC\b',
    r'\bGM\b',
    r'\bIR\b',
    r'\bORO\.',
    r'\bCAT\.',
    r'\bFTL\b',
    r'\bMED\.',
    r'\bFCL\.',
    r'\bARA\.',
    r'\bORA\.',
    r'Part-[A-Z]+\b',
    r'what does easa say',
    r'which references',
    r'according to easa',
]


@dataclass(frozen=True)
class SpecialistDecision:
    use_specialist: bool
    reason: str


def should_use_easa_specialist(text: str) -> SpecialistDecision:
    for pattern in EASA_TRIGGER_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return SpecialistDecision(True, f'matched pattern: {pattern}')
    return SpecialistDecision(False, 'no EASA-specific trigger matched')
