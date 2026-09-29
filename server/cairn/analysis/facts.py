"""Pick the sentences worth saying out loud.

Encyclopedic intros start with definitions ("X is a Y located in Z"). The good stuff is
superlatives, firsts, names, dates, and people doing things. We score sentences on
those signals and keep a short, ordered selection.
"""
from __future__ import annotations

import re

_ABBREV = r"\b(?:U\.S|U\.K|St|Mt|Ft|Dr|Mr|Mrs|Ms|Jr|Sr|No|Co|Inc|Ltd|e\.g|i\.e|vs|approx|ca)\."

SUPERLATIVE = re.compile(
    r"\b(largest|biggest|oldest|tallest|highest|deepest|longest|first|only|last|most|rarest|richest|"
    r"world's|famous|best-known|legend|legendary|unique|record)\b",
    re.I,
)
STORY = re.compile(
    r"\b(named (?:after|for)|known as|nicknamed|discovered|founded|built|erupt\w*|fled|surrender\w*|"
    r"killed|died|struck|rush|boom|abandoned|destroyed|survived|legend|story|believed|according to|"
    r"once|pioneer\w*|expedition|settlers?|miners?|tribe|people)\b",
    re.I,
)
NUMBER = re.compile(r"\b\d[\d,.]*\b")
BOILERPLATE = re.compile(
    r"^(?:the )?[^.]{0,80}\b(?:is|was) (?:a|an|the) [^.]{0,60}\b(?:located|situated|in the|in)\b", re.I
)
AVOID = re.compile(r"\b(may refer to|coordinates|census|population was|zip code|area code|see also)\b", re.I)


def split_sentences(text: str) -> list[str]:
    # Protect abbreviations, then split on sentence punctuation.
    protected = re.sub(_ABBREV, lambda m: m.group(0).replace(".", "§"), text)
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])", protected)
    return [p.replace("§", ".").strip() for p in parts if p.strip()]


def sentence_score(sentence: str, index: int) -> float:
    words = len(sentence.split())
    score = 0.0
    score += 2.0 * min(2, len(SUPERLATIVE.findall(sentence)))
    score += 1.2 * min(2, len(STORY.findall(sentence)))
    score += 0.6 * min(2, len(NUMBER.findall(sentence)))
    if index == 0 and BOILERPLATE.search(sentence):
        score -= 1.5
    if AVOID.search(sentence):
        score -= 5
    if words > 38:
        score -= (words - 38) * 0.15
    if words < 6:
        score -= 2
    if sentence.count(",") + sentence.count(";") > 5:  # lists read badly aloud
        score -= 1.5
    return score


def is_story_sentence(sentence: str) -> bool:
    """Sentences about people and events suit the storyteller voice."""
    return bool(STORY.search(sentence))


def pick_facts(text: str, max_words: int = 85, max_sentences: int = 3) -> list[str]:
    sentences = split_sentences(text)
    if not sentences:
        return []
    scored = sorted(((sentence_score(s, i), i, s) for i, s in enumerate(sentences)), key=lambda t: (-t[0], t[1]))
    chosen: list[tuple[int, str]] = []
    words = 0
    for score, i, s in scored:
        n = len(s.split())
        if score < -1 and chosen:
            break
        if words + n > max_words and chosen:
            continue
        chosen.append((i, s))
        words += n
        if len(chosen) >= max_sentences:
            break
    # Keep the original order so the story flows.
    return [s for _, s in sorted(chosen)]


def interest(text: str) -> float:
    """How 'story-rich' a text is, 0..~3. Used in notability ranking."""
    sentences = split_sentences(text)[:12]
    if not sentences:
        return 0.0
    top = sorted((sentence_score(s, i) for i, s in enumerate(sentences)), reverse=True)[:3]
    return max(0.0, sum(top) / 6)
