# FocusOS – Cognitive Load Heatmap + Daily Brain Budget

> **A Chrome / Edge browser extension + local Windows inference service that
> overlays predicted cognitive load directly onto the web pages you read.**

All inference is **private and local** – no data is sent to the cloud.  
Powered by [TRIBE v2](https://huggingface.co/facebook/tribev2) (Facebook AI,
CC-BY-NC 4.0), or a lightweight heuristic stub when the model is not installed.

---

## Features (MVP)

| Feature | Description |
|---|---|
| **On-page heatmap** | Block-level green / amber / red overlay based on predicted neural activation |
| **Tracking toggle** | Default **OFF**; flip ON in the sidebar to start analysis |
| **Daily brain budget** | Cumulative cognitive cost across a session (0–200 %) |
| **Page score** | `low` / `good` / `high` demand rating for the current page |
| **Top high-load blocks** | Ranked list of the five most demanding sections |
| **Reading strategy** | Time-of-day recommendation based on page score |

---

## Architecture

```
Browser (Chrome / Edge)               Windows machine
┌─────────────────────────┐           ┌─────────────────────────┐
│  content.js             │           │  tribe-local/app.py      │
│  · extracts text blocks │ ─POST──▶  │  FastAPI on :8787        │
│  · applies heatmap      │ ◀─JSON──  │  model.py (TRIBE v2 /   │
│                         │           │  heuristic stub)         │
│  sidebar.html / .js     │           │  scoring.py              │
│  · toggle, budget, tips │           └─────────────────────────┘
│                         │
│  background.js          │
│  · state, API relay     │
└─────────────────────────┘
```

---

## Repository Structure

```
FocusOS/
├── tribe-extension/       Chrome / Edge extension (Manifest V3)
│   ├── manifest.json
│   ├── background.js      Service worker – API relay, state
│   ├── content.js         Block extraction + heatmap overlay
│   ├── content.css        Overlay colour styles
│   ├── sidebar.html       Premium-calm sidebar UI
│   ├── sidebar.css
│   ├── sidebar.js
│   └── icons/
│
└── tribe-local/           Python FastAPI inference server (Windows)
    ├── app.py             FastAPI routes + schemas
    ├── model.py           TRIBE v2 loader + heuristic stub
    ├── scoring.py         Load-score weighting + page score
    ├── requirements.txt
    ├── start_server.bat   One-click launcher (Windows)
    └── README.md          Detailed setup guide
```

---

## Quick Start

### 1. Clone the repo

```bat
git clone https://github.com/cst0313/FocusOS.git
cd FocusOS
```

### 2. Start the local inference server

```bat
cd tribe-local
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
start_server.bat
```

Verify at `http://127.0.0.1:8787`.

### 3. Load the extension in Chrome / Edge

1. Open `chrome://extensions` (or `edge://extensions`)
2. Enable **Developer mode**
3. Click **Load unpacked** → select the `tribe-extension/` folder
4. Pin the FocusOS icon to your toolbar

### 4. Use FocusOS

1. Click the FocusOS toolbar icon to open the sidebar.
2. Toggle **Tracking → ON**.
3. Browse any page – coloured overlays appear instantly.
4. Check the sidebar for your daily brain budget and reading strategy.

---

## Cognitive Load Score Formula

```
load = 0.45 × lang  +  0.35 × exec  +  0.20 × vis
```

- **lang** – predicted language-network activation
- **exec** – predicted executive-control activation
- **vis**  – predicted visual-cortex activation

Page score = mean of the top-30 % highest block loads, scaled to 0–100.

---

## Heatmap Colours

| Colour | Load range | Meaning |
|--------|-----------|---------|
| 🟢 Green | < 0.33 | Low cognitive demand |
| 🟡 Amber | 0.33 – 0.66 | Moderate cognitive demand |
| 🔴 Red | > 0.66 | High cognitive demand |

---

## TRIBE v2 Model

TRIBE v2 is **optional** – the server works immediately with a built-in
heuristic stub.  See [`tribe-local/README.md`](tribe-local/README.md) for full
installation instructions.

> **Licence:** TRIBE v2 is CC-BY-NC 4.0 (non-commercial use only).  
> FocusOS is MIT licensed.

---

## Privacy

- All text processing happens **on your machine**.
- No analytics, telemetry, or cloud calls.
- Data is stored in `chrome.storage.local` only.

---

## Disclaimer

FocusOS is an **educational and research tool**.  
Predictions are based on average-subject fMRI patterns from TRIBE v2 and are
**not** a medical or individual diagnostic.
