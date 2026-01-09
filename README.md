# PrinceSahu_MANIT — Bill Summarization API (Gemini-backed)

[![Python Version](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/) [![Repo Size](https://img.shields.io/github/repo-size/aarav-02/PrinceSahu_MANIT)](https://github.com/aarav-02/PrinceSahu_MANIT) [![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

A focused FastAPI service that exposes a single endpoint to summarize bills/invoices/receipts using a large language model (Google Gemini or compatible LLM). This README describes how the API works, how the Gemini integration is wired, how to run and test the service locally, and how to deploy it.

---

## Table of contents

- [Overview](#overview)
- [What it does](#what-it-does)
- [Tech stack](#tech-stack)
- [Repository layout](#repository-layout)
- [Requirements](#requirements)
- [Environment & Configuration](#environment--configuration)
- [Quick start](#quick-start)
- [API spec](#api-spec)
- [Prompting & Gemini notes](#prompting--gemini-notes)
- [Examples (curl / python)](#examples-curl--python)
- [Testing](#testing)
- [Docker & Deployment](#docker--deployment)
- [Security & production considerations](#security--production-considerations)
- [Contributing](#contributing)
- [How I can help next](#how-i-can-help-next)

---

## Overview

This service receives bill text (or OCRed text from an uploaded file) and returns a structured extraction and short human-friendly summary. It uses a small prompt + LLM pattern: build a deterministic prompt asking the model to return JSON with specific fields, then parse that JSON and return it to the caller.

Primary use-cases:
- Automated expense ingestion
- Accounting assistant
- App that turns receipts into line-items and totals

---

## What it does

- Accepts bill text or file (optional OCR pipeline)
- Calls a Gemini-compatible LLM to extract fields:
  - merchant, date, total, currency, tax, line_items (name, qty, unit_price, total_price)
  - optionally categories, notes
- Returns structured JSON and a one-sentence summary
- Designed so the LLM backend can be swapped

---

## Tech stack

- Python 3.8+
- FastAPI (HTTP API)
- httpx (async HTTP client)
- pydantic (schemas)
- uvicorn (ASGI server)
- Optional: pytesseract / pdfplumber for OCR
- Testing: pytest

---

## Repository layout

Recommended layout (update to match repo contents):

```
.
├─ README.md
├─ LICENSE
├─ requirements.txt
├─ .env.example
├─ Dockerfile
├─ app/
│  ├─ main.py             # FastAPI app
│  ├─ api.py              # router(s)
│  ├─ schemas.py          # Pydantic request/response models
│  ├─ services/
│  │  ├─ gemini_client.py # wrapper to call Gemini API
│  │  └─ summarizer.py    # prompt builder and parser
│  └─ utils/
│     └─ ocr.py           # optional OCR helpers
└─ tests/
   └─ test_api.py
```

---

## Requirements

Install deps:

```bash
python -m venv .venv
source .venv/bin/activate   # macOS / Linux
.venv\Scripts\activate      # Windows
pip install -r requirements.txt
```

Example requirements (see file in repo):
- fastapi
- uvicorn[standard]
- httpx
- pydantic
- python-dotenv
- pytest

---

## Environment & configuration

Create a `.env` using `.env.example`. Important variables:

- GEMINI_API_KEY — (required) your Gemini or LLM API key
- GEMINI_API_BASE — (optional) base URL for the Gemini endpoint (if using a proxy)
- GEMINI_MODEL — (optional) model name to use
- LOG_LEVEL — optional log level

Never commit secrets to git. Use secret stores in production.

---

## Quick start (local)

1. Clone & install:

```bash
git clone https://github.com/aarav-02/PrinceSahu_MANIT.git
cd PrinceSahu_MANIT
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Create `.env` (or set env vars):

```
GEMINI_API_KEY=your_key_here
GEMINI_API_BASE=https://api.example.com  # optional
```

3. Run:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Open http://localhost:8000/docs for interactive docs.

---

## API spec

POST /summarize-bill

- Accepts:
  - JSON `{ "text": "<bill text>", "lang": "en" }`
  - (Optional) multipart/form-data file upload (image / pdf) — service can OCR and then summarize
- Returns JSON:

```json
{
  "merchant": "Acme",
  "date": "2026-01-08",
  "total": "123.45",
  "currency": "USD",
  "tax": "3.45",
  "line_items": [
    { "name": "Widget A", "qty": 2, "unit_price": "20.00", "total_price": "40.00" }
  ],
  "summary": "One-line human summary",
  "raw_model_output": {...}  // optional for debugging
}
```

---

## Prompting & Gemini notes

To keep parsing reliable:
- Ask the model to "Return JSON only" with a strict schema.
- Provide examples (few-shot) if model behavior is noisy.
- Include explicit failure modes (e.g., `"If date not found, return null"`).
- Use temperature=0 (or low) for deterministic outputs.

Example prompt (simplified):
```
Extract merchant, date (YYYY-MM-DD), total (number), currency, tax (number), and line_items (list of objects: name, qty, unit_price, total_price) from the bill text below. Return JSON only with keys: merchant, date, total, currency, tax, line_items, summary. If a field is not present, set it to null or an empty list. Bill text:
---
{bill_text_here}
---
Return valid JSON exactly matching the schema.
```

---

## Example requests

Curl:

```bash
curl -X POST "http://localhost:8000/summarize-bill" \
  -H "Content-Type: application/json" \
  -d '{"text":"Merchant: Joe''s Diner\nDate: 2026-01-02\nTotal: $45.00\nItems:\n- Burger $25.00\n- Fries $5.00\n- Drink $15.00"}'
```

Python client:

```python
import requests
r = requests.post("http://localhost:8000/summarize-bill", json={"text": raw_text})
print(r.json())
```

---

## Testing

Run unit tests:

```bash
pytest -q
```

Add tests for prompt -> model parsing logic in `tests/`.

---

## Docker & Deployment

Build:

```bash
docker build -t prince-bill-api:latest .
```

Run:

```bash
docker run -e GEMINI_API_KEY=$GEMINI_API_KEY -p 8000:8000 prince-bill-api:latest
```

For production, put the app behind a reverse proxy, attach TLS, use a secrets manager, and scale workers (Gunicorn + Uvicorn workers or a process manager).

---

## Security & production considerations

- Rate-limit clients to avoid runaway costs.
- Implement retry + exponential backoff for LLM calls.
- Redact sensitive data from logs.
- Monitor LLM usage & costs and set alerts.
- Consider a cache for repeated bills or identical inputs.

---

## Contributing

1. Fork
2. Create branch: `git checkout -b feat/gemini-api`
3. Add tests and docs
4. Open a PR
