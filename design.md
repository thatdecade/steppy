# Design Notes

Steppy is organized as a set of loosely coupled modules coordinated by a central controller.

When idle, app shows `logo.png` and a QR code that opens a local control webpage served by Steppy.

Some modules have a main() defined for standalone testing of that module.

## UI Design

### State model

Steppy uses a single controller owned state machine.
UI surfaces are dumb views that render state and forward events.

Qt app states:

* `IDLE`
* `LOADING`
* `PLAYING`
* `PAUSED`
* `RESULTS`
* `ERROR`

All state transitions are explicit controller methods.
Web requests are converted into controller commands. UI code cannot mutate state directly.

---

### Clock model

Gameplay timing uses a single clock:

```
song_time_seconds = player_time_seconds + av_offset_seconds
```

Where:

* `player_time_seconds` is reported by the embedded player
* `av_offset_seconds` is user calibration stored in persistence

Pausing freezes:

* note scheduling
* overlay rendering
* judgement processing

No secondary clocks exist.

---

### Chart model

Internal chart representation is simple and stable:

* `NoteEvent(time_seconds: float, lane: int, kind: "tap")`

Notes are:

* sorted by time
* grouped by lane for fast lookup

StepMania SM files are **serialization output only**, never core state.

---

## Modules

### steppy.py

**Purpose**
Real entrypoint that launches the full application. Supports demo mode for offline development.

**Integration**

* Creates `QApplication`
* Loads config and paths
* Instantiates controller and main window
* Starts Flask web server in background
* Wires command queue polling and starts event loop

**Standalone `main()`**
Launches full app or demo mode using fake player and chart.

---

### config.py

**Purpose**
Typed configuration loading and validation.

**Integration**

* Loads a single UTF-8 config file
* Supports environment overrides
* Used by most modules, but performs no other I/O

Uses pydantic for validation and defaults.

---

### paths.py

**Purpose**
Centralizes all application directories.

**Integration**

* Defines Charts, ChartsAuto, cache, thumbnails, logs, database paths
* Defines location of `logo.png` and web static assets
* Ensures required directories exist at startup

Uses platformdirs for OS-appropriate locations.

**Standalone `main()`**
Prints resolved paths and optionally creates them.

---

### app_controller.py

**Purpose**
Central orchestrator and state machine owner.

**Integration responsibilities**

* Accepts commands from the web server command queue
* Commands embedded player via web_player_bridge
* Loads charts via library_index or chart_engine
* Advances scheduler, judge, and overlay
* Controls idle overlay visibility and QR refresh
* Emits lighting events (no consumer yet)

Session lifecycle:

* IDLE shows logo and QR
* PLAYING shows video and overlay
* RESULTS returns to IDLE unless configured otherwise

**Standalone `main()`**
Runs a headless simulated session with fake chart and synthetic inputs.

---

### main_window.py

**Purpose**
Single window UI surface for idle, playback, and overlay.

**Integration**

* Loads UI from main_window_ui.py generated from main_window_ui.ui designer file.
* Hosts embedded web video player
* Hosts overlay renderer layered above player
* Hosts idle overlay (logo and QR) above everything when idle
* Provides non-interactive fullscreen kiosk behavior

Exposes narrow API:

* `show_idle()`, `hide_idle()`
* `set_idle_qr(image)`
* `load_video(video_id)`, `play()`, `pause()`, `seek(seconds)`

**Standalone `main()`**
Shows idle screen and toggles into a fake playback clock for layout testing.

---

### idle_overlay.py

**Purpose**
Renders `logo.png` and a QR code in a fullscreen overlay.

**Integration**

* Loads `logo.png` from paths
* Accepts a QR image from qr_code.py
* Provides layout rules so QR is readable at typical viewing distance
* Can optionally show a short URL hint under the QR

**Standalone `main()`**
Displays logo plus a sample QR code for visual verification.

---

### qr_code.py

**Purpose**
Generates the QR code image linking to the local control webpage.

**Integration**

* Builds URL from config, usually `http://<host>:<port>/`
* Generates QR as a QImage or PNG bytes and saves to temp folder.
* Assumes trusted local users. (No tokens)

**Standalone `main()`**
Generates a QR PNG for a provided URL and prints save location.

---

### web_server.py

**Purpose**
Flask backend serving the phone control web app and control API.

**Integration**

* Serves landing page and static assets
* Provides endpoints for search, play, pause, resume, restart, difficulty, stop
* Uses youtube_api.py for search and metadata
* Pushes control commands into a thread-safe queue for Qt

Security model:

* No login
* No token rotation
* Assumes trusted local network

**Standalone `main()`**
Runs the web server alone for frontend development.

---

### control_api.py

**Purpose**
Thread-safe bridge between Flask handlers and the Qt controller.

**Integration**

* Defines command types and validation
* Enqueues commands for Qt thread consumption
* Provides read-only “status snapshot” for the web UI:

  * state, current video, time, difficulty

Implementation pattern:

* Flask thread only touches control_api
* Qt polls the queue on a QTimer and executes commands

---

### web_models.py

**Purpose**
Shared request and response schemas for web API.

**Integration**

* Defines structured payloads for:

  * search results
  * play requests
  * control actions
  * status snapshots
* Keeps Flask and Qt aligned on data shape

---

### web_player_bridge.py

**Purpose**
Bridge between Python and YouTube IFrame player.

**Integration**

* Loads local HTML asset hosting IFrame player
* Uses QWebChannel for bidirectional messaging
* Exposes playback control, time polling, state events

Used by main_window and controller. The web app never touches the player directly.

**Standalone `main()`**
Debug harness showing time polling and manual play, pause, seek.

---

### youtube_api.py

**Purpose**
YouTube search and metadata for the web app.

**Integration**

* Search
* Fetch video details (title, duration, thumbnails)
* Optional playlist support for attract mode selection

Used by Flask backend. Results are cached in persistence to reduce quota and improve speed.

**Standalone `main()`**
CLI search tool printing JSON results.

---

### thumb_cache.py

**Purpose**
Thumbnail download and local caching.

**Integration**

* Fetches thumbnails on demand from URLs returned by youtube_api
* Stores locally and returns file paths or local URLs
* Web app can serve cached thumbnails for faster phone UI

---

### persistence.py

**Purpose**
SQLite storage layer.

Stores:

* favorites
* recents
* per-video offsets
* last difficulty
* generator version per chart
* cached YouTube metadata
* optional per-video BPM estimate from tap-to-beat

**Integration**

* Single database
* WAL enabled
* Forward-compatible schema

**Standalone `main()`**
Initialize schema, insert demo data, dump records.

---

### library_index.py

**Purpose**
Resolve chart source by video and difficulty.

**Integration**
Use paths.py and this search order:

1. `Charts/<video_id>/`
2. `ChartsAuto/<video_id>/`

Curated charts always override generated ones.

---

### sm_store.py

**Purpose**
Translate between internal chart model and SM files.

**Integration**

* Uses simfile
* Writes metadata: video ID, difficulty, offset, generator version, BPM estimate if known
* SM is output format only

**Standalone `main()`**
Round-trip test from memory to SM and back.

---

### chart_engine.py

**Purpose**
Owns chart selection, caching, and generation strategy per video. Provides a single “chart handle” API to gameplay.

**Integration**
Responsibilities:

* Decide chart source and lifecycle for a play session:

  * Prefer curated SM from `Charts/<video_id>/`
  * Else cached auto SM from `ChartsAuto/<video_id>/`
  * Else generate and stream notes via generator
* Provide a single interface to scheduler and overlay:

  * `StaticChart`: all notes available immediately (from SM)
  * `RollingChart`: notes are produced incrementally ahead of playhead

Rolling behavior:

* Maintains a generation buffer window, for example 30 seconds ahead.
* Produces notes in deterministic chunks keyed by:

  * `video_id`
  * `difficulty`
  * `generator_version`
  * `seed`
* After the song completes or sufficient coverage is generated, writes SM to `ChartsAuto` with metadata:

  * generator version
  * seed
  * timing assumptions
  * any per-video offset calibration applied

How it handles beat/onset without downloading audio:

* Chart engine supports two generator modes and chooses based on available signals:

  1. **Heuristic-only mode** (always available)

     * Uses a tempo model that is either:

       * user tap-to-beat calibration, or
       * a default BPM per difficulty profile
     * Notes align to a beat grid, but not musically synchronized
  2. **Playback-driven timing hints** (optional, non-audio)

     * Uses signals obtainable without raw audio:

       * player time
       * user taps to refine BPM while previewing
       * duration from YouTube metadata to bound chart
     * This is still not onset detection, but provides improved grid alignment and stability

The engine explicitly avoids any approach requiring raw media extraction.

**Standalone `main()`**
Generates a rolling chart for a fake duration and exports SM.

---

### chart_generator_fast.py

**Purpose**
Creates an instantly playable chart for any song without audio access, using a beat grid plus pattern heuristics.

**Integration**
Inputs:

* `difficulty`
* `duration_seconds` (from YouTube metadata when available)
* `seed` (derived from video_id, difficulty, generator_version)
* optional `energy_curve` (default curve if none provided)

Outputs:

* `NoteEvent` objects in requested time windows, suitable for rolling generation.
* Notes are deterministic for the same `(video_id, difficulty, generator_version)`.

How to get beat/onset data without downloading audio (practical approach used here):
This module does not attempt true onset detection. Instead it achieves usable timing via a staged approach:

1. **Immediate start with a default tempo grid**

* Select a default BPM per difficulty profile, for example:

  * easy: 120
  * medium: 140
  * hard: 160
* Generate a beat grid:

  * quarters for easy
  * eighths for medium
  * include controlled sixteenths for hard

2. **Optional tap-to-beat refinement**

* During the first 5 to 10 seconds of playback, allow the user to tap a “Calibrate Beat” button on the touchscreen.
* Each tap is timestamped using player time.
* Compute BPM from inter-tap intervals, reject outliers, and lock BPM for this video.
* Persist BPM guess in persistence keyed by video_id so future plays start with the refined BPM.

3. **Pattern generation over the grid**

* Convert beat slots into candidate note times using an intensity schedule:

  * base density by difficulty
  * controlled bursts for “chorus-like” sections using a repeating curve
* Assign lanes with strict playability rules:

  * alternate feet, avoid repeated jacks beyond difficulty threshold
  * cap consecutive notes in same lane
  * avoid doubles unless difficulty allows
  * avoid impossible transitions by penalizing opposite-lane repeats

4. **Rolling chunk generation**

* Generator receives `(t_start, t_end)` window and emits notes only for that range.
* This guarantees “no waiting” and keeps CPU low.

5. **Cache output as SM**

* Once full duration coverage is produced, export to SM:

  * store BPM used, seed, and generator_version
  * store any offsets used
* Replay becomes instant and consistent.

This creates a consistent gameplay experience without needing audio extraction, and gets closer to musical feel via tap-to-beat and caching.

**Standalone `main()`**
Generates a chart, prints density stats, and writes SM for inspection.

---

### note_scheduler.py

**Purpose**
Maintains rolling buffer of upcoming notes.

**Integration**

* Ensures N seconds of future notes available
* Handles restart, difficulty change, stop
* Bridges chart_engine to overlay and judge

**Standalone `main()`**
Simulates song playback and validates buffer never underflows.

---

### judge.py

**Purpose**
Judgement, scoring, combo, life tracking.

**Integration**
Inputs:

* scheduled notes
* timestamped input events

Outputs:

* judgement events
* summary stats
* lighting events

Pure logic, no rendering.

**Standalone `main()`**
Simulates perfect, late, and miss input streams.

---

### overlay_renderer.py

**Purpose**
Gameplay visuals.

**Integration**
Renders:

* receptors
* scrolling notes
* judgements
* combo, score, life

Uses player time as sole clock source.

**Standalone `main()`**
Renders fake chart with keyboard input for performance testing.

**Implementation note**

* Start with QPainter
* Migrate to Qt Quick only if needed

---

### input_router.py

**Purpose**
Maps dance pad keyboard input to lanes.

**Integration**

* Normalizes key repeat
* Debounces input
* Emits timestamped lane events

**Standalone `main()`**
Visual test window showing lane presses.

---

### lighting_events.py

**Purpose**
Define lighting event schema and emitter for future lighting module.

**Integration**

* Defines event types
* Exposes Qt signal emitter
* No consumers (at this time)

---

### diagnostics.py

**Purpose**
Hidden diagnostics and health UI.

**Integration**
Displays:

* version
* player state and time
* chart source
* web server status and last command time
* overlay FPS
* recent errors

**Standalone `main()`**
Displays demo diagnostics panel.

---

## Suggested dev harness commands

```
python steppy.py --demo
python -m web_player_bridge --video dQw4w9WgXcQ
python -m overlay_renderer
python -m chart_generator_fast --length 210 --difficulty hard --write-sm out.sm
python -m web_server
python -m idle_overlay
```
