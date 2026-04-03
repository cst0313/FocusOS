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

1. Accept the model licence on Hugging Face and request access to the gated
   **LLaMA 3.2** model (used by the TRIBE v2 text encoder).

2. Install TRIBE v2 from the official GitHub repository (there is no PyPI
   package named `tribev2`):

   ```bat
   pip install -U huggingface_hub
   pip install "tribev2[plotting] @ git+https://github.com/facebookresearch/tribev2.git"
   ```

   > If you don’t need 3D brain visualizations, you can omit `plotting`:
   >
   > ```bat
   > pip install "tribev2 @ git+https://github.com/facebookresearch/tribev2.git"
   > ```

3. Log in to Hugging Face (this installs the `huggingface-cli` command):

   ```bat
   huggingface-cli login
   ```

4. Restart the server. It will download the TRIBE v2 checkpoint (~1 GB) on
   first run and print `Loading TRIBE v2 model…`.

Reference implementations:
- TRIBE v2 model card: <https://huggingface.co/facebook/tribev2>
- TRIBE v2 Colab demo: <https://colab.research.google.com/github/facebookresearch/tribev2/blob/main/tribe_demo.ipynb>
- Meta blog: <https://ai.meta.com/blog/tribe-v2-brain-predictive-foundation-model/>

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
