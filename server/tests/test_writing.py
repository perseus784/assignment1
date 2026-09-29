import json
from types import SimpleNamespace

import pytest

from trailside.analysis.classify import classify
from trailside.models import Place, Stop
from trailside.writing.writer import ClaudeWriter, Editorial, TemplateWriter, spoken_name, validate_segments

PLACE = Place(id="t:ym", name="Yankee Girl Mine", lat=37.93, lon=-107.70, types=["mine"], sitelinks=3,
              extract=("The Yankee Girl Mine was one of the richest silver mines of the Red Mountain mining district. "
                       "Prospectors struck its ore body in 1882, setting off a rush that filled the valley with camps. "
                       "Its weathered wooden shaft house still stands on the hillside above the highway."))


def stop(side="left"):
    classify(PLACE)
    return Stop(place=PLACE, order=0, trigger=PLACE.latlon, radius=300, priority=2, side=side)


def test_template_writer_structure():
    script = TemplateWriter().write(stop())
    segs = [s.to_dict() for s in script.segments]
    says = [s for s in segs if s["type"] == "say"]
    assert says[0]["voice"] == "narrator"
    assert says[0]["text"].startswith(("On your left", "Look to your left", "Coming up on your left"))
    assert "one of the richest silver mines" in says[0]["text"]  # definition folded into the hook
    assert any(s["voice"] == "storyteller" for s in says)
    assert [s["sound"] for s in segs if s["type"] == "bed"][0] == "mine"
    assert [s["sound"] for s in segs if s["type"] == "sfx"] == ["blast"]
    validate_segments(segs)
    assert 20 <= len(script.spoken_text.split()) <= 130


def test_spoken_name():
    assert spoken_name("Silverton, Colorado") == "Silverton"
    assert spoken_name("Mercury (planet)") == "Mercury"


@pytest.mark.parametrize("bad", [
    [{"type": "say", "voice": "robot", "text": "hi"}],
    [{"type": "bed", "sound": "not-a-sound"}],
    [{"type": "sfx", "sound": "river"}],  # a bed used as a one-shot
    [{"type": "bed", "sound": "river"}],  # nothing spoken
    [{"type": "say", "voice": "narrator", "text": "word " * 200}],
])
def test_validate_rejects(bad):
    with pytest.raises(ValueError):
        validate_segments(bad)


class FakeMessages:
    def __init__(self, payload, stop_reason="end_turn"):
        self.payload, self.stop_reason, self.calls = payload, stop_reason, []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(stop_reason=self.stop_reason, content=[SimpleNamespace(type="text", text=json.dumps(self.payload))])


def fake_client(payload, stop_reason="end_turn"):
    messages = FakeMessages(payload, stop_reason)
    return SimpleNamespace(beta=SimpleNamespace(messages=messages)), messages


def test_claude_writer_uses_structured_output_and_validates():
    payload = {"segments": [
        {"type": "bed", "sound": "mine", "gain": 0.5},
        {"type": "say", "voice": "narrator", "text": "On your left, the Yankee Girl Mine."},
        {"type": "say", "voice": "storyteller", "text": "In 1882, prospectors struck silver here."},
        {"type": "sfx", "sound": "blast", "gain": 0.6, "wait": False},
    ]}
    client, messages = fake_client(payload)
    script = ClaudeWriter(client=client).write(stop())
    assert script.writer == "claude" and len(script.segments) == 4
    call = messages.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert call["fallbacks"] == "default"
    brief = json.loads(call["messages"][0]["content"].split("\n\n", 1)[1])
    assert brief["side_of_road"] == "left" and "Prospectors" in brief["source_text"]


def test_claude_writer_falls_back_on_refusal_or_bad_output():
    client, _ = fake_client({"segments": []}, stop_reason="refusal")
    assert ClaudeWriter(client=client).write(stop()).writer == "template"
    client, _ = fake_client({"segments": [{"type": "bed", "sound": "lava-lamp"}]})
    assert ClaudeWriter(client=client).write(stop()).writer == "template"


def test_editorial_matching_and_skip():
    ed = Editorial.load("yellowstone-geyser-country")
    of = Place(id="x", name="Old Faithful", lat=0, lon=0)
    assert ed.script_for(of).writer == "editorial"
    assert ed.script_for(Place(id="y", name="Somewhere Else", lat=0, lon=0)) is None
    assert ed.skips(Place(id="z", name="Excelsior Geyser", lat=0, lon=0))
    assert ed.intro() and len(ed.ambient()) == 5
