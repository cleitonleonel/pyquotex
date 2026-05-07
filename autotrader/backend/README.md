# Autotrader backend

FastAPI app that drives the autotrader. See the parent
[`autotrader/README.md`](../README.md) for the full picture.

## Quick start

```bash
uv sync
uv run uvicorn autotrader.main:app --reload --loop uvloop --http httptools
uv run pytest
```

Set `AUTOTRADER_PASSCODE` and `AUTOTRADER_FERNET_KEY` first (see
`../.env.example`).
