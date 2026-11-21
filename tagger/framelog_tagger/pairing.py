"""Pairing scans with log cards by frame number.

Scans named `<roll>-05.jpg`, or lab names like `004123450005.jpg`, carry their frame number at the end. Pairing by that
number (rather than by position) keeps a frame you forgot to log from shifting every scan after it one card off.
"""
from __future__ import annotations

import math
import re
from pathlib import PurePath
from typing import Optional

_TRAILING = re.compile(r"(\d+)$")

MOST = 0.6   # the share of possible matches that must line up for frame-number pairing to be used


def scan_number(name: str) -> Optional[int]:
    """The number a scan's name ends in (`Lisbon-05.jpg` -> 5, `004123450005.jpg` -> 4123450005), else None."""
    m = _TRAILING.search(PurePath(name).stem)
    return int(m.group(1)) if m else None


def by_frame(scans: list[str], entries: list[tuple[int, int]]) -> Optional[dict]:
    """Pair scans with cards by frame number, or None when that doesn't fit the roll.

    `scans` are scan names in display order, `entries` (card id, frame #) pairs. Scan N pairs with the card whose frame
    is N + offset, where the offset is the constant that makes the most numbers match (labs number from 0, or carry the
    roll's number in front: `004123450005` is frame 5 at a large offset); 0 wins a tie, then the smallest. It fits when
    every scan name ends in a number, no two the same, and at least MOST of the scans or of the frames (whichever is
    fewer) find their match. Otherwise the caller pairs by position.

    Slots run in frame order: a scan with no card and a card with no scan each get their own slot where their number
    falls, so nothing is dropped. A second card with the same frame # (a pasted copy) goes at the end.
    Returns {"offset", "matched", "slots": [[scan or None, card id or None], ...]}.
    """
    nums = [scan_number(n) for n in scans]
    if not scans or not entries or any(n is None for n in nums) or len(set(nums)) != len(nums):
        return None
    frames = {f for _, f in entries}
    best = None
    for d in {f - n for n in nums for f in frames}:
        score = sum(n + d in frames for n in nums)
        key = (-score, abs(d), -d)   # most matches, then the smallest offset (+1 before -1)
        if best is None or key < best[0]:
            best = (key, d, score)
    _, offset, matched = best
    if matched < max(1, math.ceil(MOST * min(len(nums), len(frames)))):
        return None

    first, extra = {}, []
    for eid, f in entries:
        if f in first:
            extra.append(eid)
        else:
            first[f] = eid
    by_key = {n + offset: s for n, s in zip(nums, scans)}
    slots = [[by_key.get(k), first.get(k)] for k in sorted(set(by_key) | set(first))]
    slots += [[None, eid] for eid in extra]
    return {"offset": offset, "matched": matched, "slots": slots}
