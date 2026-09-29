"""Turn a place into an episode script (a list of segments the app plays in order)."""
from __future__ import annotations

import json
import logging
import os
import re
import zlib
from pathlib import Path
from typing import Optional, Protocol

from ..analysis.classify import era
from ..analysis.facts import is_story_sentence, pick_facts, split_sentences
from ..audio.library import LIBRARY, THEME_SOUNDS, names
from ..config import ROOT, settings
from ..models import Place, Script, Segment, Stop
from .style import HOOK_PREFIX_AHEAD, HOOK_PREFIX_SIDE, HOOKS_AHEAD, HOOKS_SIDE, STYLE_GUIDE

log = logging.getLogger(__name__)

EDITORIAL_DIR = ROOT / "editorial"
MAX_WORDS = 130


def spoken_name(name: str) -> str:
    """'Silverton, Colorado' -> 'Silverton'; 'Mercury (planet)' -> 'Mercury'."""
    name = re.sub(r"\s*\([^)]*\)$", "", name)
    return re.sub(r",\s*[A-Z][\w .'-]+$", "", name)


def _pick(options: list[str], key: str) -> str:
    return options[zlib.crc32(key.encode()) % len(options)]


def sound_plan(place: Place) -> tuple[list[str], Optional[str]]:
    """Beds (max 2, distinct) and one accent for a place, from its themes."""
    beds: list[str] = []
    accent: Optional[str] = None
    for rank, theme in enumerate(place.themes):
        bed, acc = THEME_SOUNDS.get(theme, (None, None))
        if bed and bed not in beds and len(beds) < 2:
            beds.append(bed)
        if acc and not accent and rank < 2:  # accents only for what the place is mostly about
            accent = acc
    return beds or ["wind"], accent


def sources_for(place: Place) -> list[dict]:
    if not place.url:
        return []
    return [{"title": place.name, "url": place.url, "license": place.license, "source": place.source}]


class Writer(Protocol):
    name: str

    def write(self, stop: Stop) -> Script: ...


class TemplateWriter:
    """Deterministic writer: picks the most story-worthy sentences and frames them.
    Works offline with no API keys; quality depends on the source text."""

    name = "template"

    def hook_and_facts(self, stop: Stop) -> tuple[str, list[str]]:
        place = stop.place
        name = spoken_name(place.name)
        sentences = split_sentences(place.extract)
        # "X is a Y ..." reads badly after "Coming up, X." — fold the definition into the hook.
        if sentences:
            m = re.match(rf"^(?:The )?{re.escape(name)}\b[^.]*?\s(?:is|was)\s+(.+?)\.?$", sentences[0])
            if m and len(sentences[0].split()) <= 32:
                prefix = _pick(HOOK_PREFIX_SIDE, place.id).format(side=stop.side) if stop.side else _pick(HOOK_PREFIX_AHEAD, place.id)
                article = "the " if sentences[0].startswith("The ") else ""
                return f"{prefix} {article}{name}, {m.group(1)}.", pick_facts(" ".join(sentences[1:]), max_words=70, max_sentences=2)
        hook = _pick(HOOKS_SIDE, place.id).format(side=stop.side, name=name) if stop.side else _pick(HOOKS_AHEAD, place.id).format(name=name)
        return hook, pick_facts(place.extract, max_words=75, max_sentences=3)

    def write(self, stop: Stop) -> Script:
        place = stop.place
        hook, facts = self.hook_and_facts(stop)
        beds, accent = sound_plan(place)

        segs = [Segment("music", sound="transition", gain=0.5, wait=False), Segment("bed", sound=beds[0], gain=0.55)]
        if len(beds) > 1:
            segs.append(Segment("bed", sound=beds[1], gain=0.3))
        segs += [Segment("pause", seconds=0.8), Segment("say", voice="narrator", text=hook), Segment("pause", seconds=0.4)]

        # Narrator carries orientation/context; the storyteller carries people and events.
        story_idx = [i for i, f in enumerate(facts) if is_story_sentence(f)] or [len(facts) - 1]
        accent_used = False
        for i, fact in enumerate(facts):
            voice = "storyteller" if i in story_idx else "narrator"
            segs.append(Segment("say", voice=voice, text=fact))
            if accent and not accent_used and voice == "storyteller":
                segs.append(Segment("sfx", sound=accent, gain=0.6, wait=False))
                accent_used = True
            segs.append(Segment("pause", seconds=0.35))
        segs.append(Segment("pause", seconds=1.2))
        return Script(title=place.name, segments=segs, sources=sources_for(place), writer=self.name)


class ClaudeWriter:
    """Writes episodes with Claude, grounded in the source text, returning structured JSON.
    Falls back to the template writer on any failure or refusal."""

    name = "claude"

    def __init__(self, client=None, model: Optional[str] = None):
        import anthropic

        self.anthropic = anthropic
        self.client = client or anthropic.Anthropic()
        self.model = model or settings.claude_model
        self.fallback = TemplateWriter()
        self.schema = episode_schema()
        self.sound_menu = "\n".join(f"- {s.name} ({s.kind}): {s.description}" for s in LIBRARY.values() if s.kind in ("bed", "sfx"))

    def brief(self, stop: Stop) -> dict:
        p = stop.place
        beds, accent = sound_plan(p)
        return {
            "place": p.name,
            "what_it_is": p.description or ", ".join(p.types[:3]),
            "side_of_road": stop.side or "ahead",
            "era": era(p),
            "suggested_beds": beds,
            "suggested_accent": accent,
            "source_text": p.extract[:3000],
        }

    def write(self, stop: Stop) -> Script:
        try:
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=4000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                system=[{"type": "text", "text": f"{STYLE_GUIDE}\nAvailable sounds:\n{self.sound_menu}", "cache_control": {"type": "ephemeral"}}],
                output_config={"effort": "medium", "format": {"type": "json_schema", "schema": self.schema}},
                messages=[{"role": "user", "content": "Write the episode for this stop.\n\n" + json.dumps(self.brief(stop), ensure_ascii=False)}],
            )
            if response.stop_reason == "refusal":
                raise ValueError("refused")
            text = next(b.text for b in response.content if b.type == "text")
            segments = validate_segments(json.loads(text)["segments"])
            return Script(title=stop.place.name, segments=segments, sources=sources_for(stop.place), writer=self.name)
        except (self.anthropic.APIError, ValueError, KeyError, StopIteration, json.JSONDecodeError) as exc:
            log.warning("Claude writer failed for %s (%s); using template", stop.place.name, exc)
            return self.fallback.write(stop)


def episode_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "segments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": ["bed", "sfx", "say", "pause", "music"]},
                        "voice": {"type": "string", "enum": ["narrator", "storyteller"]},
                        "text": {"type": "string"},
                        "sound": {"type": "string", "enum": names()},
                        "gain": {"type": "number"},
                        "seconds": {"type": "number"},
                        "wait": {"type": "boolean"},
                    },
                    "required": ["type"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["segments"],
        "additionalProperties": False,
    }


def validate_segments(raw: list[dict]) -> list[Segment]:
    """Reject scripts the player can't or shouldn't play."""
    segs = []
    beds = accents = 0
    for d in raw:
        seg = Segment.from_dict(d)
        if seg.type == "say":
            if not seg.text or seg.voice not in ("narrator", "storyteller"):
                raise ValueError("say needs text and a known voice")
        elif seg.type in ("bed", "sfx", "music"):
            sound = LIBRARY.get(seg.sound or "")
            expected = {"bed": "bed", "sfx": "sfx", "music": "music"}[seg.type]
            if not sound or sound.kind != expected:
                raise ValueError(f"unknown {seg.type} sound {seg.sound!r}")
            seg.gain = min(1.0, max(0.05, seg.gain))
            beds += seg.type == "bed"
            accents += seg.type == "sfx"
        elif seg.type == "pause":
            seg.seconds = min(4.0, max(0.1, seg.seconds or 0.6))
        segs.append(seg)
    words = sum(len((s.text or "").split()) for s in segs if s.type == "say")
    if not words:
        raise ValueError("no narration")
    if words > MAX_WORDS or beds > 2 or accents > 2:
        raise ValueError(f"too long or too busy ({words} words, {beds} beds, {accents} accents)")
    return segs


# ---------------------------------------------------------------------------------------
# Editorial: hand-curated scripts win over generated ones
# ---------------------------------------------------------------------------------------


class Editorial:
    def __init__(self, data: dict):
        self.data = data
        self.by_key = {k.lower(): v for k, v in data.get("stops", {}).items()}

    @classmethod
    def load(cls, name: Optional[str], directory: Path = EDITORIAL_DIR) -> Optional["Editorial"]:
        if not name:
            return None
        path = directory / f"{name}.json"
        return cls(json.loads(path.read_text())) if path.exists() else None

    def script_for(self, place: Place) -> Optional[Script]:
        for key in (place.wikidata, place.id, place.name):
            entry = self.by_key.get((key or "").lower())
            if entry:
                return Script(
                    title=entry.get("title", place.name),
                    segments=validate_segments(entry["segments"]),
                    sources=entry.get("sources") or sources_for(place),
                    writer="editorial",
                )
        return None

    def skips(self, place: Place) -> bool:
        """Editors can drop places whose story is already told elsewhere in the tour."""
        skip = {k.lower() for k in self.data.get("skip", [])}
        return any((k or "").lower() in skip for k in (place.wikidata, place.id, place.name))

    def intro(self) -> Optional[Script]:
        e = self.data.get("intro")
        return Script(e.get("title", "Welcome"), validate_segments(e["segments"]), writer="editorial") if e else None

    def ambient(self) -> list[tuple[str, Script]]:
        return [(a["id"], Script(a["title"], validate_segments(a["segments"]), writer="editorial")) for a in self.data.get("ambient", [])]

    def meta(self) -> dict:
        return {k: self.data[k] for k in ("name", "region", "description", "note") if k in self.data}


def intro_script(tour_name: str, stops: list[Stop]) -> Script:
    highlights = [spoken_name(s.place.name) for s in sorted(stops, key=lambda s: -s.place.score)[:3]]
    listed = ", ".join(highlights[:-1]) + f" and {highlights[-1]}" if len(highlights) > 1 else (highlights[0] if highlights else "")
    bed, _ = sound_plan(max((s.place for s in stops), key=lambda p: p.score)) if stops else (["pad"], None)
    segs = [
        Segment("music", sound="intro", gain=0.7, wait=False),
        Segment("bed", sound="pad", gain=0.35),
        Segment("pause", seconds=2.2),
        Segment("say", voice="narrator", text=f"Welcome to the {tour_name}." if tour_name.lower().endswith(("highway", "road", "trail", "loop", "byway")) and not tour_name.lower().startswith("the ") else f"Welcome to {tour_name}."),
        Segment("bed", sound=bed[0], gain=0.4),
        Segment("say", voice="narrator", text=f"Along this drive there are {len(stops)} stories waiting, including {listed}." if listed else "There are stories waiting along this drive."),
        Segment("pause", seconds=0.4),
        Segment("say", voice="storyteller", text="Keep your eyes on the road. I'll tell you when something's worth a look."),
        Segment("pause", seconds=1.0),
    ]
    return Script(title=f"Welcome to {tour_name}", segments=segs, writer="template")


def get_writer(kind: Optional[str] = None) -> Writer:
    kind = kind or settings.writer
    if kind == "claude" or (kind == "auto" and (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))):
        try:
            return ClaudeWriter()
        except Exception as exc:  # SDK missing or misconfigured
            log.warning("Claude writer unavailable (%s); using template writer", exc)
    return TemplateWriter()
