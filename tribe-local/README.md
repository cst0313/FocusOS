# tribe-local – FocusOS Local Inference Server

## Overview

This directory contains the **Windows-ready Python FastAPI server** that the
FocusOS browser extension connects to for cognitive load inference.

The server runs entirely on your machine – no data is sent to the cloud.

---

## Quick Start (Windows)

### 1. Prerequisites

- Python 3.10 or newer (<https://python.org/downloads/>)
- pip (bundled with modern Python)

### 2. Create & activate a virtual environment

```bat
cd tribe-local
python -m venv .venv
.venv\Scripts\activate
```

### 3. Install dependencies

```bat
pip install -r requirements.txt
```

### 4. Start the server

```bat
start_server.bat
```

The server starts at `http://127.0.0.1:8787`.  
Open your browser and navigate to `http://127.0.0.1:8787` to verify it is running.

---

## TRIBE v2 Model (Optional but Recommended)

By default, the server runs in **heuristic stub mode** – it estimates cognitive
load from text statistics without loading any AI model, which works on any
hardware instantly.

To enable **real TRIBE v2 inference**:

1. Accept the model licence on Hugging Face and log in:

   ```bat
   pip install tribev2
   huggingface-cli login
   ```

2. Ensure you have accepted the **LLaMA 3.2 gated model** access as well
   (TRIBE v2 uses LLaMA 3.2 internally for the text encoder).

3. Restart the server.  It will print `Loading TRIBE v2 model…` on first run.

> **Hardware note:** TRIBE v2 is a large multi-modal model.  A CUDA-capable
> NVIDIA GPU (8 GB+ VRAM) is strongly recommended for reasonable inference
> latency.  CPU-only inference is possible but slow.

To force stub mode even when TRIBE v2 is installed:

```bat
set FOCUSOS_STUB=1
start_server.bat
```

---

## API Reference

### `GET /`

Health check.

```json
{ "status": "ok", "server": "FocusOS", "mode": "heuristic_stub" }
```

### `POST /predict`

**Request body**

```json
{
  "page_url": "https://example.com/article",
  "timestamp": "2025-01-01T12:00:00",
  "blocks": [
    {
      "id": "focusos-block-0",
      "text": "The theory of relativity...",
      "domPath": "article > p:nth-of-type(1)",
      "position": 0,
      "tagName": "p"
    }
  ]
}
```

**Response body**

```json
{
  "page_url": "https://example.com/article",
  "page_score": 67.4,
  "page_label": "high",
  "blocks": [
    {
      "id": "focusos-block-0",
      "load": 0.74,
      "lang": 0.88,
      "exec": 0.65,
      "vis": 0.31
    }
  ],
  "model_mode": "heuristic_stub",
  "timestamp": "2025-01-01T12:00:01"
}
```

---

## Licence

TRIBE v2 is released under **CC-BY-NC 4.0** (non-commercial use only).  
FocusOS itself is open-source under the **MIT Licence**.
