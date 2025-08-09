# Autonomer Live‑Trading‑Bot (Binance, GCP) — BUILD MODE (Step 1)

Status: **MVP‑Skeleton** (API + Risk Engine + Deploy Script)
Version: `0.1.0`

## Features (Step 1)
- FastAPI Service:
  - `GET /status` → `{"ok": true, "version": "0.1.0"}`
  - `GET /metrics` → Prometheus‑Format (uptime, request_count)
  - `GET /risk/summary` → aktuelle Risk‑Konfiguration & State
  - `POST /paper/start|stop` → Skelett‑Orchestrierung
  - `POST /live/start|stop` → Skelett‑Orchestrierung
  - `POST /live/flat` → **Kill‑Switch (Sofort‑Flat)**
  - `GET /live/position` → Stub (Step 2: echte Daten)
- JSON‑Logging (stdout), Healthcheck in Dockerfile
- Cloud Run Deploy‑Script inkl. Secrets & SA

> **Guardrails aktiv (Skelett):** Kill‑Switch, Daily Loss Cap, Max Risk/Trade (Risk‑Engine).  
> **Noch nicht enthalten (kommt in Schritt 2):** Binance‑Client, Order‑Router, WS‑Reconnect, Persistence, RL‑Loop.

---

## Lokale Entwicklung

### 1) Setup
```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env  # optional lokal
