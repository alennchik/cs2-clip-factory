"""Step 4: suggest the on-screen hook line ("Speaker: line") from speech, loudness and title.

Deterministic heuristics; the reviewer picks or rewrites in Telegram. An LLM can be added later
behind a flag if the evaluation set shows it beats these rules.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

import numpy as np

from clipfactory.transcribe import Segment, Transcript

MAX_HOOK_CHARS = 60
EMOJI_RE = re.compile("[\U0001f000-\U0001faff\U00002600-\U000027bf\U0001f1e6-\U0001f1ff️‍⃣]+")
LEADING_JUNK_RE = re.compile(r"^[\s\-–—.,…:;]+")
SPACE_RE = re.compile(r"\s+")
CLAUSE_END_RE = re.compile(r"[.!?…]$")


@dataclass(frozen=True)
class HookCandidate:
    text: str
    start: float
    end: float
    score: float
    source: str  # "speech" | "title"


def clean_line(text: str) -> str:
    line = EMOJI_RE.sub(" ", text)
    line = SPACE_RE.sub(" ", line).strip()
    line = LEADING_JUNK_RE.sub("", line)
    line = line.rstrip(" ,;:-–—")
    if line.endswith(".") and not line.endswith(".."):
        line = line[:-1].rstrip()
    return line


def shorten(text: str, limit: int = MAX_HOOK_CHARS) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:-–—")
    return cut + "…"


def format_hook(speaker: str, line: str) -> str:
    return shorten(f"{speaker}: {line}" if speaker else line)


def _length_score(words: int) -> float:
    if 3 <= words <= 8:
        return 1.0
    if words in (2, 9, 10):
        return 0.7
    if 11 <= words <= 14:
        return 0.4
    return 0.1


def _energy_score(envelope: np.ndarray | None, window: float, start: float, end: float) -> float:
    """0..1: how much louder this span peaks than the clip's typical level."""
    if envelope is None or envelope.size == 0:
        return 0.0
    i0 = max(int(start / window), 0)
    i1 = max(i0 + 1, int(math.ceil(end / window)))
    span = envelope[i0:i1]
    if span.size == 0:
        return 0.0
    base = float(np.median(envelope))
    spread = max(float(np.percentile(envelope, 90) - np.percentile(envelope, 10)), 3.0)
    peak = float(np.percentile(span, 90))
    return float(min(max((peak - base) / spread, 0.0), 1.0))


def _clauses(seg: Segment) -> list[tuple[str, float, float, list[float]]]:
    """Split long segments at sentence punctuation using word timings."""
    words = seg.words
    if len(seg.text.split()) <= 10 or not words:
        return [(seg.text, seg.start, seg.end, [w.prob for w in words])]
    clauses: list[tuple[str, float, float, list[float]]] = []
    current: list = []
    for word in words:
        current.append(word)
        if CLAUSE_END_RE.search(word.text) and len(current) >= 2:
            clauses.append(
                (
                    " ".join(w.text for w in current),
                    current[0].start,
                    current[-1].end,
                    [w.prob for w in current],
                )
            )
            current = []
    if current:
        clauses.append(
            (
                " ".join(w.text for w in current),
                current[0].start,
                current[-1].end,
                [w.prob for w in current],
            )
        )
    return clauses


def suggest_hooks(
    transcript: Transcript | None,
    *,
    title: str = "",
    speaker: str = "",
    envelope: np.ndarray | None = None,
    window: float = 0.1,
    k: int = 3,
) -> list[HookCandidate]:
    candidates: list[HookCandidate] = []
    if transcript is not None:
        for seg in transcript.segments:
            for text, start, end, probs in _clauses(seg):
                line = clean_line(text)
                words = len(line.split())
                if words < 2 or len(line) > 90:
                    continue
                score = _length_score(words)
                if "!" in line:
                    score += 0.25
                if "?" in line:
                    score += 0.15
                score += 0.6 * _energy_score(envelope, window, start, end)
                if probs and sum(probs) / len(probs) < 0.45:
                    score *= 0.6  # the recognizer was unsure; don't put a misheard line on screen
                candidates.append(
                    HookCandidate(format_hook(speaker, line), start, end, round(score, 4), "speech")
                )

    title_line = clean_line(title)
    if title_line and 1 <= len(title_line.split()) <= 14:
        candidates.append(HookCandidate(shorten(title_line), 0.0, 0.0, 0.55, "title"))

    picked: list[HookCandidate] = []
    seen: set[str] = set()
    for cand in sorted(candidates, key=lambda c: (-c.score, c.start)):
        key = re.sub(r"\W+", "", cand.text.lower())
        if not key or key in seen:
            continue
        seen.add(key)
        picked.append(cand)
        if len(picked) == k:
            break
    return picked
