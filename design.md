## Overview

Steppy is organized as a set of loosely coupled modules coordinated by a central controller.

---

## Modules

### steppy.py

**Purpose**
Application entrypoint. Initializes Qt, loads config, creates controller and windows.

Launches full app or demo mode without YouTube access.

---

### config.py

**Purpose**
Typed configuration loading and validation.

**Implementation notes**
Uses pydantic. No side effects beyond file read.

---

### paths.py

**Purpose**
Defines and creates all application directories.

**Implementation notes**
Centralizes filesystem layout using platformdirs.

---

### app_controller.py

**Purpose**
Owns state machine and orchestrates all subsystems.

**Implementation notes**
Only module allowed to mutate application state.

**Standalone main()**
Runs a headless simulated play session for logic testing.

---

### ui_touchscreen.py

**Purpose**
Touchscreen UI for browsing and in game controls.

**Implementation notes**
Pure view layer. Emits signals only.

**Standalone main()**
Displays mock browse results and control panel.

---

### ui_tv_player.py

**Purpose**
Fullscreen TV window hosting player and overlay.

**Implementation notes**
Non interactive surface. No input handling.

**Standalone main()**
Loads test video or simulated player clock.

---

### web_player_bridge.py

**Purpose**
Python to JavaScript bridge for embedded YouTube player.

**Implementation notes**
Uses QWebChannel and local HTML asset.

**Standalone main()**
Displays player state and time polling diagnostics.

---

### youtube_api.py

**Purpose**
YouTube search and metadata retrieval.

**Implementation notes**
Wraps YouTube Data API v3 calls.

**Standalone main()**
Command line search tool printing JSON results.

---

### thumb_cache.py

**Purpose**
Downloads and caches video thumbnails locally.

**Implementation notes**
Provides local file paths for UI rendering.

---

### persistence.py

**Purpose**
SQLite storage for favorites, recents, offsets, metadata.

**Implementation notes**
Single connection model, WAL enabled.

**Standalone main()**
Initializes schema and prints sample records.

---

### library_index.py

**Purpose**
Resolves chart sources from curated and auto folders.

**Implementation notes**
Curated charts always override generated ones.

---

### sm_store.py

**Purpose**
Reads and writes StepMania SM files.

**Implementation notes**
Uses simfile. SM is serialization format only.

**Standalone main()**
Round trip test from memory to SM and back.

---

### chart_engine.py

**Purpose**
Unified chart access layer for cached or generated charts.

**Implementation notes**
Returns static or rolling chart handles.

---

### chart_generator_fast.py

**Purpose**
Instant chart generation without audio analysis.

**Implementation notes**
Deterministic patterns based on difficulty and time.

**Standalone main()**
Generates charts and exports SM for inspection.

---

### note_scheduler.py

**Purpose**
Maintains buffered note window ahead of playhead.

**Implementation notes**
Guarantees minimum future note availability.

---

### judge.py

**Purpose**
Judgement logic, scoring, combo, life tracking.

**Implementation notes**
Pure logic. Emits events upward.

**Standalone main()**
Simulates perfect and imperfect input streams.

---

### overlay_renderer.py

**Purpose**
Renders notes, receptors, judgements, score overlay.

**Implementation notes**
Uses player time as single clock source.

**Standalone main()**
Runs animated fake chart for performance testing.

---

### input_router.py

**Purpose**
Maps keyboard input from dance pad to lanes.

**Implementation notes**
Normalizes repeat and debouncing.

**Standalone main()**
Visualizes lane presses in a test window.

---

### lighting_events.py

**Purpose**
Defines lighting event schema and signal emitter.

**Implementation notes**
No consumers initially. Future WLED hook point.

**Standalone main()**
Emits test events on a timer.

---

### diagnostics.py

**Purpose**
Hidden diagnostics and health monitoring UI.

**Implementation notes**
Read only inspection of internal state.

**Standalone main()**
Displays mock diagnostics data.
