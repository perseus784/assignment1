# Cairn Tour Format (`cairn.tour/2`)

The contract between the **engine** (which researches, writes, and produces tours) and the
**app** (which only downloads and plays them). A tour is a directory:

```
<tour-id>/
  manifest.json          everything below, plus the list of files
  sounds/<name>.mp3      ambience beds, sound effects, music stings (shared by all stops)
  voice/<key>-<n>.mp3    narration, when the engine rendered voices (optional)
  map.pmtiles            offline vector basemap for the area (optional)
```

All paths in the manifest are relative to `manifest.json`. The app caches every file listed in
`assets` to play the tour with no connection.

## manifest.json

```jsonc
{
  "format": "cairn.tour/2",
  "id": "million-dollar-highway",
  "version": 1,
  "name": "Million Dollar Highway",
  "region": "Colorado, USA",
  "description": "10 stories across 26 km of road, including …",
  "language": "en",
  "generatedAt": "2026-09-29T16:18:30Z",
  "generator": { "engine": "cairn-engine/0.2", "writer": "template|claude", "tts": "device|kokoro", "source": "live|fixture:…" },
  "kind": "tour",                        // "tour", or "cell" for a "just drive" map-cell pack
  "cell": null,                          // e.g. "330_-970" when kind is "cell"
  "travelSpeedMps": 13.0,                // speed the stories were paced for

  "center": [37.94, -107.67],
  "bounds": { "south": 37.78, "west": -107.75, "north": 38.06, "east": -107.63 },
  "route": [[37.8121, -107.6628], …],       // [lat, lon] of the drive, or null for area tours
  "routeLengthM": 26201,
  "map": { "pmtiles": "map.pmtiles", "schema": "protomaps", "maxzoom": 14 },   // or null

  "voices": {
    "narrator":    { "style": "calm documentary narrator", "device": { "pitch": 0.92, "rate": 0.97 } },
    "storyteller": { "style": "warm storyteller",          "device": { "pitch": 1.05, "rate": 1.0 } }
  },

  "intro":  { "id": "intro", …episode },   // plays when you enter `bounds` (null for cells)
  "outro":  { "audio": "sounds/outro.mp3" },  // plays after the last stop (null for cells)
  "stops":  [ …stop ],
  "ambient": [ { "id": "fact-bison", "title": "…", "episode": { … } } ],  // fillers for quiet stretches

  "attribution": ["Text adapted from Wikipedia articles, CC BY-SA 4.0. …"],
  "assets": [ { "path": "sounds/river.mp3", "bytes": 240941, "sha256": "…", "type": "audio/mpeg" } ],
  "totalBytes": 2676672
}
```

### stop

```jsonc
{
  "id": "yankee-girl-mine-3",
  "name": "Yankee Girl Mine",
  "order": 3,                               // position along the tour
  "category": "mining",                     // geology|water|nature|railway|mining|roads|town|history|religion|culture|landmark
  "themes": ["mining", "mountain"],         // what it sounds like, strongest first
  "summary": "historic silver mine in the Red Mountain mining district",
  "place":   { "lat": 37.929, "lon": -107.699 },            // the thing itself (map pin, navigation)
  "trigger": { "lat": 37.9288, "lon": -107.6962, "radius": 300 },   // geofence on the road
  // Cells and area tours add "reach": 2500, i.e. how far off the driver's path it can be announced
  "priority": 2,                            // 1–3; higher plays first when several trigger together
  "side": "left",                           // left/right of the road when known
  "along": 14535,                           // meters from the route start
  "sources": [ { "title": "…", "url": "…", "license": "CC BY-SA 4.0" } ],
  "episode": { …episode }
}
```

The app starts a stop's episode when the device enters its `trigger` circle, or earlier when
moving towards it (look-ahead = speed × a few seconds). If the trigger has a `reach`, it also
starts when the place is ahead and the driver's path will pass within `reach` meters of it.
Each stop plays once per trip. In "just drive" mode, stops are remembered across drives.

### episode

```jsonc
{
  "title": "Yankee Girl Mine",
  "writer": "template|claude|editorial",
  "estimatedSeconds": 25.4,
  "transcript": "On your left, the Yankee Girl Mine, …",
  "segments": [
    { "type": "music", "sound": "transition", "audio": "sounds/transition.mp3", "gain": 0.5, "wait": false },
    { "type": "bed",   "sound": "mine", "audio": "sounds/mine.mp3", "gain": 0.55, "loop": true },
    { "type": "bed",   "sound": "wind", "audio": "sounds/wind.mp3", "gain": 0.3, "loop": true },
    { "type": "pause", "seconds": 0.8 },
    { "type": "say",   "voice": "narrator", "text": "On your left, …", "audio": "voice/03-0.mp3", "duration": 4.1 },
    { "type": "say",   "voice": "storyteller", "text": "Prospectors struck its ore body in 1882, …" },
    { "type": "sfx",   "sound": "blast", "audio": "sounds/blast.mp3", "gain": 0.6, "wait": false },
    { "type": "pause", "seconds": 1.2 }
  ]
}
```

Segments play **in order**:

| type | behaviour |
| --- | --- |
| `bed` | Start a looping ambience that continues under everything after it. At most two at once (a third replaces the oldest). All beds fade out when the episode ends. |
| `sfx` | One-shot effect. `wait: false` lets narration continue over it. |
| `music` | Musical sting (`intro`, `transition`, `outro`). Same `wait` rule. |
| `say` | A spoken line by `narrator` or `storyteller`. If `audio` is present, play it; otherwise synthesise `text` with an on-device voice using the role's `device` pitch/rate. Beds duck under speech. |
| `pause` | Silence for `seconds` (beds keep playing). |

Loops are rendered seamless; players should loop from just after the start to just before the
end of the decoded file to skip MP3 encoder padding (the app uses 60 ms).
