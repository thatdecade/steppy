## Steppy

Steppy is a rhythm game frontend for dance pad setups.

When idle or between songs, Steppy displays a QR code that opens a local webpage for song selection and controls.

---

## Features

### Gameplay

* YouTube video playback with a rhythm note overlay.
* Deterministic auto chart timings

### Idle

* Shows logo when not playing.
* Shows a generated QR code linking to the local control webpage.
* Optionally continues a muted attract playlist behind the idle screen.

### Web App

* Phone friendly control UI served locally from the Steppy PC.
* Search, browse, and playlists are powered by YouTube Data API via the local backend.
* Controls: play, pause, resume, restart, change difficulty, stop.
* Shows basic status: current song, elapsed time, difficulty, and playing state.
* Designed for trusted local networks. No login or pairing flow.

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
* Stop and return to idle

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
pip install flask qrcode
```

Async libraries are intentionally avoided initially. Qt signals and timers are preferred.
