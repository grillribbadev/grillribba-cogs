from __future__ import annotations
import re
from typing import Iterable

_WORDY = re.compile(r"[a-z0-9]+")

def normalize(text: str) -> str:
    return "".join(_WORDY.findall(text.lower()))

def is_guess_match(guess: str, target: str, aliases: Iterable[str] = ()) -> bool:
    g = normalize(guess)
    opts = [normalize(target), *(normalize(a) for a in aliases)]
    if any(g == o for o in opts):
        return True
    return any(g in o or o in g for o in opts)
