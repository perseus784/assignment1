"""Runtime settings, all overridable with environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # server/


def _env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(name, default)


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(_env("CAIRN_DATA_DIR", str(ROOT / "data"))))
    # Wikipedia/Nominatim require a descriptive User-Agent with contact details.
    user_agent: str = field(
        default_factory=lambda: _env(
            "CAIRN_USER_AGENT", "CairnTourEngine/0.2 (https://github.com/perseus784/assignment1)"
        )
    )
    wikipedia_lang: str = field(default_factory=lambda: _env("CAIRN_WIKI_LANG", "en"))
    nominatim_url: str = field(default_factory=lambda: _env("CAIRN_NOMINATIM_URL", "https://nominatim.openstreetmap.org"))
    # The public OSRM demo server is for light use only; point this at your own instance in production.
    osrm_url: str = field(default_factory=lambda: _env("CAIRN_OSRM_URL", "https://router.project-osrm.org"))

    # Script writing: "auto" uses Claude when an API key is configured, else the template writer.
    writer: str = field(default_factory=lambda: _env("CAIRN_WRITER", "auto"))
    claude_model: str = field(default_factory=lambda: _env("CAIRN_CLAUDE_MODEL", "claude-opus-5-5"))

    # Neural TTS (Kokoro via kokoro-onnx). Without these the app narrates with the device voice.
    kokoro_model: str | None = field(default_factory=lambda: _env("CAIRN_KOKORO_MODEL"))
    kokoro_voices: str | None = field(default_factory=lambda: _env("CAIRN_KOKORO_VOICES"))
    narrator_voice: str = field(default_factory=lambda: _env("CAIRN_NARRATOR_VOICE", "am_michael"))
    storyteller_voice: str = field(default_factory=lambda: _env("CAIRN_STORYTELLER_VOICE", "af_heart"))

    # Optional offline basemap: a local or remote .pmtiles planet file + the `pmtiles` CLI.
    pmtiles_source: str | None = field(default_factory=lambda: _env("CAIRN_PMTILES_SOURCE"))

    public_url: str = field(default_factory=lambda: _env("CAIRN_PUBLIC_URL", ""))

    @property
    def tours_dir(self) -> Path:
        return self.data_dir / "tours"


settings = Settings()
