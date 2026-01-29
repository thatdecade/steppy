## Steppy

Steppy is a rhythm game frontend for dual monitor / dance pads setups.

---

## Features

### Touchscreen

* Touchscreen is the primary and only user interaction surface.
* Large targets, no mouse, no keyboard required.
* On screen keyboard provided by the app.

### Duall Monitor

* Upper display (TV): gameplay video and note overlay only.
* Lower display (touchscreen): browsing, controls, pause menu.
* No duplicated playback surfaces.

### YouTube

* Custom search and browse UI using YouTube Data API.
* Attract mode plays a curated YouTube playlist muted.
* User can search and select any song instantly.

### Gameplay

* Gameplay starts immediately on song selection.
* Charts are generated instantly using fast heuristics.
* Rolling chart generation continues in the background.

### Charts

* Auto generated charts are cached in StepMania SM format.
* Curated charts override generated charts automatically.
* Cached charts load instantly on replay.

### Controls

* Play
* Pause
* Resume
* Restart song
* Change difficulty while paused
* Stop and return to browse

### Deterministic timing

* Gameplay timing is derived solely from the player clock.
* AV offset calibration supported and persisted.
* No secondary clocks or drift sources.

---

## Python libraries

Python 3.11

```bash
pip install PyQt6 PyQt6-WebEngine
pip install google-api-python-client
pip install simfile
pip install platformdirs pydantic
```

Async libraries are intentionally avoided initially. Qt signals and timers are preferred.
