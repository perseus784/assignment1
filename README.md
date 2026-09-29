# Trailside: GPS audio tour guide

Start it when you enter a national park and drive. As you approach each place, Trailside plays a short spoken story about it. Tours are downloaded ahead of time, so **nothing on the road needs an internet connection**: GPS works without signal, and narration uses the phone's built-in text-to-speech voice.

It's an offline-first **Progressive Web App**: plain HTML/JS, no build step, no dependencies.

## Try it

```bash
npm start          # serves on http://localhost:8080 (python3 -m http.server)
npm test           # unit tests (Node 18+)
```

1. Open the app, press **Download** on a tour (this caches it for offline use).
2. To test from home, open **Settings → Simulate a drive**, then press **Start tour**. The app drives the tour's demo route (Madison Junction → Old Faithful → Craig Pass in Yellowstone) and narrates as it goes.
3. On a phone, open the site over HTTPS and choose **Add to Home Screen**. Service workers and GPS both require HTTPS (or `localhost`). Any static host works, e.g. GitHub Pages.

## How it works

```
GPS fix ──► enrichFix (derive speed/heading) ──► TourEngine.update ──► Narrator queue ──► speechSynthesis
                                                   │
                                                   └──► radar + "coming up" list
```

| File | Role |
| --- | --- |
| `js/engine.js` | Pure trigger logic, unit-tested. Decides what to narrate for each fix. |
| `js/narrator.js` | One-at-a-time speech queue: chime, sentence chunking, skip/mute, drops stale stories. |
| `js/location.js` | Real GPS (`watchPosition`) or a simulated drive along a pack's `demoRoute`. |
| `js/packs.js` | Tour index, download/remove/load packs via the Cache API, pack validation. |
| `js/radar.js` | Heading-up canvas radar. Needs no map tiles, so it works offline. |
| `sw.js` | Service worker: caches the app shell and serves downloaded packs offline. |

**When does a story trigger?**
- **Geofence:** you're inside a place's `radius`.
- **Look-ahead (driving):** when moving faster than about 11 km/h, a place also triggers if it's within `radius + speed × leadSeconds` (capped at 600 m) *and* within 50° of your heading. At highway speed the story starts before you get there, and places behind you stay quiet.
- If several places trigger at once, higher `priority` plays first. Each story plays once per trip, and the history survives app restarts. Use **Reset trip** to clear it.
- If a queued story's place is already 2 km behind you by the time its turn comes, it is skipped.
- After 4 minutes with no story inside the park, a general "Did you know?" fact from `ambient` plays.
- Simulated drives use a separate, throwaway history, so testing doesn't use up the stories for your real trip.

## Tour pack format

Packs are JSON files listed in `packs/index.json`. See `packs/yellowstone-geyser-country.json` for a full example.

```jsonc
{
  "id": "unique-id",
  "name": "Display name",
  "bounds": { "north": 0, "south": 0, "east": 0, "west": 0 }, // park area, gates intro + ambient facts
  "intro":  { "title": "Welcome", "story": "Played when you enter the bounds" },
  "pois": [
    { "id": "old-faithful", "name": "Old Faithful", "category": "geology",
      "lat": 44.4605, "lon": -110.8281, "radius": 400, "priority": 3,
      "story": "Text that will be spoken…" }
  ],
  "ambient":   [{ "id": "fact-1", "title": "…", "story": "…" }],
  "demoRoute": [{ "lat": 0, "lon": 0 }]   // optional, used by "Simulate a drive"
}
```

`npm test` validates every pack in the index and simulates its demo route, checking that every place is reached.

> The Yellowstone sample uses approximate coordinates and is meant for demos. Verify locations and facts against official NPS sources before relying on them.

## Known limitations and next steps

- **Screen must stay on.** Browsers pause GPS when a web app is in the background. Trailside holds a screen wake lock during a tour, which works well on a car mount. For true background narration (screen off, other apps open), wrap the app with **Capacitor** and use a background-geolocation plugin plus native TTS. The engine and packs carry over unchanged.
- **iOS voices:** the higher-quality "Enhanced" voices must be downloaded once in iOS Settings → Accessibility → Spoken Content.
- **Offline maps:** the radar avoids needing tiles. A real map could come from MapLibre GL + a per-park **PMTiles** file bundled into the pack.
- **Richer audio:** packs could ship pre-recorded or pre-generated MP3s for more natural narration, with TTS as the fallback.
- **Content pipeline:** generate draft stories from NPS data (e.g. the NPS API "places" endpoint) with an LLM, then fact-check by hand before publishing.
- Roadmap ideas: route-aware triggering (snap to road so a place across a canyon doesn't fire), story length tuned to time-to-arrival, multiple languages, kid mode.
