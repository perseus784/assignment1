# Cairn: a GPS audio trail guide for any drive

*A cairn is the stack of stones travellers leave to mark the way. Cairn marks the places along
your route and tells you their stories.*

Start it as you set off. As you approach each place, Cairn plays a short produced
episode about it, like a podcast that knows where you are. A calm **narrator** tells you what's
coming up and on which side. A warm **storyteller** tells you why it matters. Ambience and
effects fit the place: river water, birdsong, mountain wind, a geyser erupting, a steam
train's whistle, pickaxes in a mine, road crews blasting rock, a frontier town.

Tours can be made for **anywhere**. The **engine** (server) researches a place and does all
the work: finding places, analysing and ranking them, planning the route and timing, writing
scripts, designing sound, and recording narration. It packages the result in a
[standard tour format](docs/tour-format.md). The **app** just downloads and plays it,
**fully offline** on the road.

```
 ┌────────────── engine (server/) ──────────────┐          ┌──────── app (app/) ────────┐
 │ discover  Wikipedia geosearch + Wikidata     │          │ search / "near me"          │
 │           Nominatim (places), OSRM (roads)   │  tour    │ download pack → Cache API   │
 │ analyse   classify · score · de-duplicate    │  pack    │ GPS / simulated drive       │
 │ plan      interval scheduling on the road,   │ ───────▶ │ geofences + look-ahead      │
 │           left/right, geofences              │ manifest │ episode player: beds, sfx,  │
 │ write     Claude (grounded) or template      │ + audio  │   music, ducked narration   │
 │ produce   procedural sound design, TTS, MP3  │          │ map (MapLibre), navigation  │
 └──────────────────────────────────────────────┘          └─────────────────────────────┘
```

## Run it

```bash
python3 -m venv .venv && .venv/bin/pip install -r server/requirements.txt
npm start            # engine + app on http://localhost:8000
npm test             # app (node) + engine (pytest) tests
```

Open http://localhost:8000. Two demo tours are bundled: **Yellowstone: Geyser Country** (hand-edited
scripts) and **Million Dollar Highway**, Colorado (written by the engine). To try one from
home, open **Settings → Simulate a drive** and press **Start tour**.

Three ways to use it:

- **Just drive.** Tap **Just drive** and go, with no plan and no destination. The engine divides
  the world into ~11 km map cells. The app fetches the cells on the road ahead of you and
  plays stories as you pass: a lake 2 km off the highway plays while it's in view, not once
  you've gone by. Fetched cells are saved, so dead zones don't interrupt anything, and what
  you've heard is remembered, so your daily commute doesn't repeat itself.
- **Plan a drive.** Type **From** (or leave it empty for your current location) and **To**, e.g.
  "Dallas, TX" → "Denton, TX". The engine routes it on real roads and picks stories for
  that exact highway. Stories are paced for the road's real speed, so a highway drive is
  spaced out more than a slow park road.
- **Tour a place.** Type any place ("Zion National Park", "Amalfi Coast") and press
  **Create tour**, or use **Create a tour around me**.

Builds take about 10–40 seconds, and the app saves every tour for offline use.
**Settings → Use demo data** makes the engine search its built-in sample places instead of
live Wikipedia/OSM.

On a phone, serve over HTTPS (GPS and offline mode require it) and use **Add to Home Screen**.

### Engine from the command line

```bash
cd server
../.venv/bin/python -m cairn build --query "Glacier National Park"            # live data
../.venv/bin/python -m cairn build --route drive.json --name "Going-to-the-Sun Road"
../.venv/bin/python -m cairn build --from "Dallas, TX" --to "Denton, TX"           # a drive from A to B
../.venv/bin/python -m cairn build --cell 330_-970                                  # one "just drive" map cell (Lewisville, TX)
../.venv/bin/python -m cairn sounds --out /tmp/sounds                          # audition the sound library
../.venv/bin/python -m cairn serve --port 8000
```

### Configuration (environment variables)

| Variable | Purpose |
| --- | --- |
| `ANTHROPIC_API_KEY` | Enables the Claude script writer (`claude-opus-5-5`), grounded in the source text. Without it the deterministic template writer is used. |
| `CAIRN_WRITER` | `auto` (default), `claude`, or `template`. |
| `CAIRN_KOKORO_MODEL`, `CAIRN_KOKORO_VOICES` | Paths to Kokoro-82M model files (see `server/requirements.txt`). Enables pre-rendered studio narration; without them the app uses on-device voices. |
| `CAIRN_NARRATOR_VOICE`, `CAIRN_STORYTELLER_VOICE` | Kokoro voice ids (default `am_michael`, `af_heart`). |
| `CAIRN_PMTILES_SOURCE` | A Protomaps `.pmtiles` build. With the `pmtiles` CLI installed, each tour gets its own offline vector map. |
| `CAIRN_USER_AGENT` | Contact string sent to Wikipedia/Nominatim (required by their policies). |
| `CAIRN_OSRM_URL`, `CAIRN_NOMINATIM_URL` | Point at your own instances in production. The public ones are for light use only. |

## How the engine works (`server/cairn/`)

1. **Discover** (`sources/`): tile the area (or a corridor along the route) with search circles and
   ask Wikipedia what's there, then fetch article intros and Wikidata facts: what kind of thing
   it is, how many language editions cover it, heritage status, year built.
   `fixtures/` replays saved data for demos and tests.
2. **Analyse** (`analysis/`)
   - `classify.py` weighs Wikidata types, descriptions and text into sound *themes* (geyser,
     river, train, mining, construction, town…) and a display category.
   - `rank.py` scores notability and "story-ness", and merges duplicates.
   - `facts.py` picks the sentences worth saying out loud: superlatives, firsts, people and events.
3. **Plan** (`analysis/plan.py`)
   - **Route mode.** Each place is projected onto the road, and each story "occupies" the
     stretch of road driven while it plays. The best set of non-overlapping stories is chosen
     with **weighted interval scheduling** (exact DP, checked against brute force in the tests).
     A second pass slides stories that just missed into the next free gap while the place is
     still close. Triggers sit on the road, and the engine knows if a place is on your left
     or right.
   - **Area mode.** Places are picked with spatial diversity (maximal marginal relevance), then
     ordered with nearest-neighbour plus **2-opt**. The order is routed on real roads, then
     re-planned in route mode.
   - **From A to B.** Both ends are geocoded and routed with OSRM. The road's average speed
     (distance ÷ typical driving time) sets how much road each story needs, so highways get
     fewer, better-spaced stories.
   - **Map cells** (`cells.py`, for "just drive"). Places inside one 0.1° square are selected
     with spatial diversity. Since the road isn't known, each stop carries a `reach` (how far
     off the road it can still be seen), and the app announces it as you head past within
     that distance.
   - Geofences are sized per category and shrunk so neighbours never overlap.
4. **Write** (`writing/`): one house style for both writers. The narrator orients, the
   storyteller carries the story, and each episode is 55–95 words. The Claude writer uses
   structured JSON output and is told to use only facts from the source text. Every script,
   from Claude or an editor, is validated before use. `editorial/` holds hand-curated
   scripts that override generated ones.
5. **Produce** (`audio/`)
   - A library of **35 procedurally synthesised sounds**: 22 seamless ambience beds, 10 effects
     and 3 music stings. There are no samples, so there are no licences to clear. Drop real
     recordings into `server/assets/sounds/<name>.wav` to override any of them.
   - Narration is optionally rendered with Kokoro, and everything is encoded to MP3.

## The app (`app/`)

A PWA in plain ES modules with no build step:
- `player.js` mixes episodes live with Web Audio (beds duck under narration).
- `speech.js` picks two distinct on-device voices for the two roles.
- `engine.js` handles geofences, look-ahead and pass-by triggering.
- `drive.js` runs "just drive": it fetches the map cells ahead of you, saves them offline and
  retries while the engine builds them.
- `map.js` uses vendored MapLibre with OpenFreeMap tiles online, a pack's PMTiles offline, or a
  plain canvas. The route and stops always draw.
- `sw.js` provides offline caching, including Range requests for PMTiles.
- **Navigate** hands off to Google Maps, Apple Maps or Waze. Cairn keeps narrating
  alongside it.

## The API

The server is a plain HTTP API (interactive reference at `/docs`), and the web app is one client
of it.

| Call | What it does |
| --- | --- |
| `POST /v1/tours` | Build a tour. Body: `{"query": "Zion National Park"}`, `{"origin": "Dallas, TX", "destination": "Denton, TX"}` (names or `[lat, lon]`), `{"center": [lat, lon], "radius_m": 20000}` or `{"route": [[lat, lon], …]}`. Returns a job. |
| `GET /v1/jobs/{id}` | Build progress. |
| `GET /v1/tours` | Built tours. |
| `GET /v1/cells/{cell}` | "Just drive" pack for one map cell (e.g. `330_-970`). Answers `building` (202, poll again), `ready` (with the manifest URL) or `empty`. |
| `GET /v1/explore?lat=…&lon=…` | Quick ranked places near a point, with no audio. |
| `GET /v1/geocode?q=…` | Place lookup. |
| `GET /tours/{id}/manifest.json` | A tour pack ([format](docs/tour-format.md)). |

## Known limitations

- **Screen on, for now.** Browsers pause GPS for background web apps, so tours hold a wake lock.
  For narration with the screen off, and for CarPlay / Android Auto, wrap the app natively
  (e.g. Capacitor with a background-geolocation plugin). The pack format and engine are unchanged.
- **Content licensing.** Wikipedia text is CC BY-SA 4.0. Packs carry attribution, and adapted
  text must stay share-alike. Self-host Nominatim/OSRM for real traffic.
- **Facts.** Generated tours are only as accurate as their sources. Editorial review is
  recommended for anything you publish. Demo coordinates are approximate.
- **Not exercised in the development sandbox:**
  - live Wikipedia/OSM calls (the parsers are tested against recorded response shapes);
  - live Claude calls (tested with a fake client);
  - Kokoro narration;
  - PMTiles extraction.

  The synthesised sounds were checked spectrally (levels, clipping, seamless loops), not by ear.
