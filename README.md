## Steppy

Steppy is a rhythm game frontend for dance pad setups.

When idle or between songs, Steppy displays a QR code that opens a local webpage for song selection and controls.

---

## Template assets not included

This repo does not include third-party template files or assets.

Steppy is prototyped with the UI and assets from "Affan - PWA Mobile HTML Template" by designing-world (care.designingworld@gmail.com)

Those purchased template files (including any images, icons, fonts, SVGs, CSS, JS, and HTML) are not redistributed here.  If you own a license for the template, you can copy your own local template files into an `assets/` directory. 

Steppy will still run without the assets, but it will look a lot more plain.

Links:
- https://themeforest.net/user/designing-world
- https://themeforest.net/item/affan-pwa-mobile-html-template/29715548

---

## Features

### Gameplay

* YouTube video playback with a rhythm note overlay.
* Deterministic auto chart timings
* Gameplay starts immediately on song selection.
* Charts are generated instantly using fast heuristics.
* Rolling chart generation continues in the background.

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
