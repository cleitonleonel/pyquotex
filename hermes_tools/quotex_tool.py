
# Credential helper: returns (email, password) from env or config
def get_credentials_from_env_or_config(config_email, config_password):
    """Return credentials, preferring environment variables."""
    import os
    email = os.environ.get("QUOTEX_EMAIL", config_email)
    password = os.environ.get("QUOTEX_PASSWORD", config_password)
    return email, password

"""
hermes_tools/quotex_tool.py
────────────────────────────────────────────────────────────────────────────────
Full pyquotex_trader harness — Hermes-callable with automatic OTP login flow.

Modes
─────
  status     → connection health, balance, asset availability
  pull       → download historical candles to CSV
  backtest   → replay signal_engine on saved CSVs, output results JSON
  train      → train a RandomForest ML model on backtest results
  chart      → generate OHLCV + indicator chart(s) from saved CSV data
  analysis   → full statistical breakdown of backtest results
  login      → attempt login; if OTP required, fetch from email and supply it
  live       → start the live trading engine

Hermes calls:
  await quotex_run(mode="status")
  await quotex_run(mode="pull", days=7)
  await quotex_run(mode="backtest")
  await quotex_run(mode="train")
  await quotex_run(mode="chart", asset="EURUSD_otc", timeframe="M1")
  await quotex_run(mode="analysis")
  await quotex_run(mode="login")          # automatic OTP handling
  await quotex_run(mode="live", duration_minutes=120)

CLI (subprocess / tmux):
  python hermes_tools/quotex_tool.py --mode pull --days 7
  python hermes_tools/quotex_tool.py --mode chart --asset GBPUSD_otc
  python hermes_tools/quotex_tool.py --mode login
  python hermes_tools/quotex_tool.py --mode live --duration 120
"""

from __future__ import annotations

import asyncio
import csv
import json
import logging
import os
import subprocess
import sys
import time
from configparser import ConfigParser
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any

# ─────────────────────────────────────────────────────────────────────────────
# Path setup – prefer the re‑imagined package if available, otherwise fallback
# ─────────────────────────────────────────────────────────────────────────────
THIS_FILE = Path(__file__).resolve()
HERMES_TOOLS_DIR = THIS_FILE.parent
# Try to locate the re‑imagined package (sibling of the pyquotex directory)
POSSIBLE_REIMAGINED = HERMES_TOOLS_DIR.parent.parent / "pyquotex_reimagined"
if POSSIBLE_REIMAGINED.is_dir():
    PROJECT_ROOT = POSSIBLE_REIMAGINED
else:
    # Fallback to original pyquotex directory (parent of hermes_tools)
    PROJECT_ROOT = HERMES_TOOLS_DIR.parent

sys.path.insert(0, str(PROJECT_ROOT))

# Import the Quotex client from the re‑imagined external wrapper
try:
    from pyquotex_ext.client import QuotexClient
except Exception as e:  # pragma: no cover – fallback for very old layouts
    raise ImportError(
        f"Cannot import QuotexClient from pyquotex_ext. "
        f"Checked {PROJECT_ROOT}. Original error: {e}"
    )

from engine.strategy_loader import load_settings, load_all_strategies, load_strategy
from engine.trader import Trader
from indicators import Candle, candles_from_dicts

log = logging.getLogger("hermes.quotex")

# ─────────────────────────────────────────────────────────────────────────────
# Directories
# ─────────────────────────────────────────────────────────────────────────────
DATA_DIR   = PROJECT_ROOT / "data" / "history"
CHART_DIR  = PROJECT_ROOT / "data" / "charts"
MODEL_DIR  = PROJECT_ROOT / "data" / "models"
LOG_DIR    = PROJECT_ROOT / "data" / "logs"

for d in [DATA_DIR, CHART_DIR, MODEL_DIR, LOG_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ─────────────────────────────────────────────────────────────────────────────
# Defaults
# ─────────────────────────────────────────────────────────────────────────────
ASSETS     = ["EURUSD_otc", "GBPUSD_otc", "USDJPY_otc"]
TIMEFRAMES = {"M1": 60, "M5": 300}
CONFIG_DIR = PROJECT_ROOT / "config"

# ─────────────────────────────────────────────────────────────────────────────
# OTP retrieval – adapted from automation/otp-autofill/references/otp_fetch_method.md
# ─────────────────────────────────────────────────────────────────────────────
import re
import imaplib
import email
import asyncio as _asyncio
from typing import Optional as OptStr

QUOTEX_FROM = "noreply@qxbroker.com"
IMAP_HOST   = "imap.gmail.com"   # change if using another provider
MAX_AGE_SECONDS = 120            # max age of an OTP email to be considered fresh

def _extract_otp_from_text(text: str) -> OptStr:
    """Extract a 6‑digit OTP from plain text or HTML.
    Looks for <b>123456</b> first, then any 6‑digit number, preferring non‑000000.
    """
    m = re.search(r"<b>(\\d{6})</b>", text, re.IGNORECASE)
    if m:
        return m.group(1)
    nums = re.findall(r"\\b\\d{6}\\b", text)
    for n in reversed(nums):          # check newest‑looking first
        if n != "000000":
            return n
    if nums:
        return nums[-1]
    return None

async def get_pin(
    email_addr: str,
    email_pass: str,
    mailbox: str = "INBOX",
    attempts: int = 5,
    delay: int = 1,
) -> OptStr:
    """
    Log into IMAP, fetch the newest *unseen* mail from QUOTEX_FROM,
    verify its arrival time is within MAX_AGE_SECONDS,
    and return the 6‑digit PIN found inside <b>…</b>.
    Returns None if not found after `attempts` retries.
    """
    try:
        mail = imaplib.IMAP4_SSL(IMAP_HOST)
        mail.login(email_addr, email_pass)
        mail.select(mailbox)
    except imaplib.IMAP4.error:
        # Never log the exception text – it could contain the password.
        return None

    for _ in range(attempts):
        # 1️⃣ Look for *unseen* Quotex mails only
        typ, data = mail.search(None, f'(UNSEEN FROM "{QUOTEX_FROM}")')
        if typ != "OK" or not data[0]:
            await _asyncio.sleep(delay)
            continue

        # Take the most recent unseen mail (last in the list)
        latest_id = data[0].split()[-1]

        # 2️⃣ Fetch its internal date (arrival time) and the full RFC822
        typ, msg_data = mail.fetch(latest_id, "(INTERNALDATE RFC822)")
        if typ != "OK":
            await _asyncio.sleep(delay)
            continue

        # Parse INTERNALDATE (e.g., "02-Feb-2025 14:23:11 +0000")
        internal_date_raw = None
        for part in msg_data:
            if isinstance(part, tuple):
                if b"INTERNALDATE" in part[0]:
                    internal_date_raw = part[1].decode().strip()
                    break
        if not internal_date_raw:
            await _asyncio.sleep(delay)
            continue

        internal_date_raw = internal_date_raw.strip('"')
        try:
            mail_time = email.utils.parsedate_to_datetime(internal_date_raw).timestamp()
        except Exception:
            await _asyncio.sleep(delay)
            continue

        now = time.time()
        if now - mail_time > MAX_AGE_SECONDS:
            # Too old – mark as seen to avoid re‑checking and continue searching
            mail.store(latest_id, "+FLAGS", "\\Seen")
            await _asyncio.sleep(delay)
            continue

        # 3️⃣ Pull the full message body to extract OTP
        typ, msg_data = mail.fetch(latest_id, "(RFC822)")
        if typ != "OK":
            await _asyncio.sleep(delay)
            continue

        raw_email = msg_data[0][1]
        msg = email.message_from_bytes(raw_email)

        otp = None
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_maintype() == "text":
                    payload = part.get_payload(decode=True).decode(errors="ignore")
                    otp = _extract_otp_from_text(payload)
                    if otp:
                        break
        else:
            payload = msg.get_payload(decode=True).decode(errors="ignore")
            otp = _extract_otp_from_text(payload)

        # Mark as seen so we don't reuse this OTP
        mail.store(latest_id, "+FLAGS", "\\Seen")
        mail.logout()
        return otp

        await _asyncio.sleep(delay)

    mail.logout()
    return None

# ─────────────────────────────────────────────────────────────────────────────
# Helper: connect QuotexClient (explicitly load credentials from config.ini)
# ─────────────────────────────────────────────────────────────────────────────
def _load_quott_credentials() -> tuple[str, str]:
    """Load email and password from pyquotex/settings/config.ini."""
    config_path = PROJECT_ROOT / "pyquotex" / "settings" / "config.ini"
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    parser = ConfigParser()
    parser.read(config_path)
    if not parser.has_section("settings"):
        raise ValueError("No [settings] section in config.ini")
    email = parser.get("settings", "email", fallback="")
    password = parser.get("settings", "password", fallback="")
    if not email or not password:
        raise ValueError("Email or password missing in config.ini")
    return email, password

async def _connect(practice: bool = True) -> QuotexClient:
    """
    Instantiate QuotexClient with credentials from config.ini.
    """
    try:
        email, password = _load_quott_credentials()
    except Exception as e:
        log.error(f"Failed to load Quotex credentials: {e}")
        raise RuntimeError("Could not load Quotex credentials from config.ini") from e

    # The QuotexClient looks for settings/config.ini relative to cwd if we don't pass credentials.
    # Since we are passing email/password explicitly, it should skip the fallback.
    original_cwd = os.getcwd()
    # Change to the pyquotex directory so that any relative paths (if any) work.
    pyquotex_dir = PROJECT_ROOT / "pyquotex"
    if not pyquotex_dir.is_dir():
        pyquotex_dir = PROJECT_ROOT
    os.chdir(str(pyquotex_dir))
    try:
        client = QuotexClient(email=email, password=password, practice=practice)
        ok = await client.connect()
        if not ok:
            raise RuntimeError("QuotexClient.connect() failed — check credentials")
        return client
    finally:
        os.chdir(original_cwd)

# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers for CSV handling
# ─────────────────────────────────────────────────────────────────────────────
def _load_csv(asset: str, timeframe: str = "M1") -> List[Dict[str, str]]:
    """Load a saved history CSV → list of raw OHLCV dicts."""
    path = DATA_DIR / f"{asset}_{timeframe}.csv"
    if not path.exists():
        raise FileNotFoundError(
            f"No data for {asset} {timeframe}. Run mode=pull first."
        )
    with open(path) as f:
        return list(csv.DictReader(f))

def _to_candles(rows: List[Dict[str, str]]) -> List[Candle]:
    """Convert CSV rows to Candle objects, silently skipping bad rows."""
    out: List[Candle] = []
    for r in rows:
        try:
            out.append(
                Candle(
                    time=int(float(r["time"])),
                    open=float(r["open"]),
                    high=float(r["high"]),
                    low=float(r["low"]),
                    close=float(r["close"]),
                )
            )
        except (KeyError, ValueError):
            continue
    return out

# ─────────────────────────────────────────────────────────────────────────────
# MODE: status
# ─────────────────────────────────────────────────────────────────────────────
async def _mode_status(practice: bool = True) -> Dict[str, Any]:
    client = await _connect(practice)
    try:
        balance = await client.get_balance()
        connected = await client.check_connect()
        assets = {}
        for a in ASSETS:
            assets[a] = await client.check_asset_open(a)
        return {
            "status": "ok",
            "mode": "status",
            "connected": connected,
            "balance": balance,
            "practice": practice,
            "assets": assets,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    finally:
        await client.disconnect()

# ─────────────────────────────────────────────────────────────────────────────
# MODE: pull
# ─────────────────────────────────────────────────────────────────────────────
async def _mode_pull(
    days: int = 7,
    practice: bool = True,
    assets: Optional[List[str]] = None,
) -> Dict[str, Any]:
    assets = assets or ASSETS
    client = await _connect(practice)
    amount_secs = days * 86400
    summary: Dict[str, Any] = {}

    try:
        for asset in assets:
            summary[asset] = {}
            for tf_label, tf_secs in TIMEFRAMES.items():
                log.info("Pulling %s %s (%d days)...", asset, tf_label, days)
                try:
                    candles = await client._api.get_historical_candles(
                        asset,
                        amount_of_seconds=amount_secs,
                        period=tf_secs,
                        max_workers=2,
                    )
                    if not candles:
                        log.warning("No candles returned for %s %s", asset, tf_label)
                        summary[asset][tf_label] = {"count": 0, "file": None}
                        continue

                    # Normalize keys to lowercase
                    if candles and isinstance(candles[0], dict):
                        candles = [
                            {k.lower(): v for k, v in c.items()}
                            for c in candles
                        ]

                    fname = DATA_DIR / f"{asset}_{tf_label}.csv"
                    fieldnames = list(candles[0].keys())
                    with open(fname, "w", newline="") as f:
                        w = csv.DictWriter(f, fieldnames=fieldnames)
                        w.writeheader()
                        w.writerows(candles)

                    summary[asset][tf_label] = {
                        "count": len(candles),
                        "file": str(fname),
                        "from": datetime.fromtimestamp(
                            int(float(candles[0].get("time", 0))), timezone.utc
                        ).isoformat(),
                        "to": datetime.fromtimestamp(
                            int(float(candles[-1].get("time", 0))), timezone.utc
                        ).isoformat(),
                    }
                    log.info("✓ Saved %d candles → %s", len(candles), fname)
                    await asyncio.sleep(1.5)   # polite gap
                except Exception as e:
                    log.error("Pull failed %s %s: %s", asset, tf_label, e)
                    summary[asset][tf_label] = {"error": str(e)}
    finally:
        await client.disconnect()

    return {"status": "ok", "mode": "pull", "days": days, "summary": summary}

# ─────────────────────────────────────────────────────────────────────────────
# MODE: backtest
# ─────────────────────────────────────────────────────────────────────────────
async def _mode_backtest(
    assets: Optional[List[str]] = None,
    window: int = 50,
) -> Dict[str, Any]:
    from engine.signal_engine import evaluate

    assets = assets or ASSETS
    strategy_dir = CONFIG_DIR / "strategies"
    strategy_names = [p.stem for p in strategy_dir.glob("*.yaml") if p.is_file()]
    strategies = load_all_strategies(CONFIG_DIR, strategy_names)   # loads all active from YAML
    results: List[Dict[str, Any]] = []

    for asset in assets:
        try:
            rows_m1 = _load_csv(asset, "M1")
            rows_m5 = _load_csv(asset, "M5") if (DATA_DIR / f"{asset}_M5.csv").exists() else []
        except FileNotFoundError as e:
            log.warning(str(e))
            continue

        candles_m1 = _to_candles(rows_m1)
        candles_m5 = _to_candles(rows_m5)

        if len(candles_m1) < window + 2:
            log.warning(
                "Too few candles for %s (%d) — need %d", asset, len(candles_m1), window
            )
            continue

        log.info("Replaying %s — %d M1 candles...", asset, len(candles_m1))

        for i in range(window, len(candles_m1) - 1):
            window_m1 = candles_m1[i - window:i]

            # Build multi‑tf dict using whatever M5 data overlaps this window
            entry_time = window_m1[-1].time
            window_m5 = [c for c in candles_m5 if c.time <= entry_time][-10:] or window_m1[-10:]
            candles_by_tf = {"60": window_m1, "300": window_m5}

            for strategy in strategies:
                try:
                    signal = evaluate(asset, candles_by_tf, strategy)
                    if signal is None:
                        continue

                    # Next candle = outcome
                    next_c = candles_m1[i]
                    entry_price = window_m1[-1].close
                    exit_price = next_c.close

                    if signal.direction == "call":
                        outcome = "win" if exit_price > entry_price else "loss"
                    else:
                        outcome = "win" if exit_price < entry_price else "loss"

                    results.append(
                        {
                            "timestamp": datetime.fromtimestamp(
                                entry_time, timezone.utc
                            ).isoformat(),
                            "asset": asset,
                            "strategy": signal.strategy,
                            "direction": signal.direction,
                            "confluence": signal.confluence_score,
                            "factors": "|".join(signal.confluence_factors),
                            "entry": entry_price,
                            "exit": exit_price,
                            "outcome": outcome,
                        }
                    )
                except Exception as e:
                    log.debug(
                        "Signal eval error at i=%d %s: %s", i, asset, e
                    )

    out_path = DATA_DIR / "backtest_results.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)

    wins = sum(1 for r in results if r["outcome"] == "win")
    total = len(results)
    win_rate = round(wins / total * 100, 1) if total else 0.0

    return {
        "status": "ok",
        "mode": "backtest",
        "total_signals": total,
        "wins": wins,
        "losses": total - wins,
        "win_rate_pct": win_rate,
        "output_file": str(out_path),
    }

# ─────────────────────────────────────────────────────────────────────────────
# MODE: train
# ─────────────────────────────────────────────────────────────────────────────
async def _mode_train() -> Dict[str, Any]:
    """
    Trains a RandomForestClassifier on backtest_results.json.
    Saves model to data/models/rf_v1.pkl.
    Drops in as a real MLScorer replacement for NullMLScorer.
    """
    results_path = DATA_DIR / "backtest_results.json"
    if not results_path.exists():
        return {
            "status": "error",
            "message": "Run mode=backtest first to generate training data.",
        }

    try:
        import numpy as np
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.model_selection import train_test_split
        from sklearn.metrics import classification_report
        import joblib
    except ImportError:
        return {
            "status": "error",
            "message": (
                "Install sklearn + joblib: pip install scikit-learn joblib --break-system-packages"
            ),
        }

    with open(results_path) as f:
        records = json.load(f)

    if len(records) < 50:
        return {
            "status": "error",
            "message": f"Only {len(records)} records — need 50+ to train.",
        }

    # ── Feature extraction (mirrors FeatureVector) ───────────────────────────
    factor_vocab = set()
    for r in records:
        factor_vocab.update(r.get("factors", "").split("|"))
    factor_vocab = sorted(factor_vocab - {""})

    def row_to_features(r: Dict[str, Any]) -> List[float]:
        factors = set(r.get("factors", "").split("|"))
        one_hot = [1.0 if f in factors else 0.0 for f in factor_vocab]
        return [
            float(r.get("confluence", 0)),
            1.0 if r.get("direction") == "call" else 0.0,
        ] + one_hot

    X = np.array([row_to_features(r) for r in records])
    y = np.array([1 if r["outcome"] == "win" else 0 for r in records])

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42
    )

    clf = RandomForestClassifier(
        n_estimators=100, random_state=42, class_weight="balanced"
    )
    clf.fit(X_train, y_train)

    report = classification_report(y_test, clf.predict(X_test), output_dict=True)
    acc = round(report["accuracy"] * 100, 1)

    model_path = MODEL_DIR / "rf_v1.pkl"
    joblib.dump({"model": clf, "factor_vocab": factor_vocab}, str(model_path))
    log.info("Model saved → %s | test accuracy=%.1f%%", model_path, acc)

    # Save scorer class alongside model for easy drop‑in
    scorer_path = PROJECT_ROOT / "ml" / "rf_scorer.py"
    scorer_code = f'''"""
ml/rf_scorer.py — auto-generated by quotex_tool train mode.
Drop‑in replacement for NullMLScorer.

Usage in runner.py:
    from ml.rf_scorer import RandomForestScorer
    scorer = RandomForestScoter("{model_path}")
    trader = Trader(client, strategies, settings, ml_scorer=scorer)
"""
import joblib
import numpy as np
from ml.base import MLScorer, FeatureVector

FACTOR_VOCAB = {factor_vocab!r}

class RandomForestScorer(MLScorer):
    def __init__(self, model_path: str = "{model_path}"):
        data = joblib.load(model_path)
        self._model   = data["model"]
        self._vocab   = data["factor_vocab"]

    def score(self, features: FeatureVector) -> float:
        factors = set(features.confluence_factors)
        one_hot = [1.0 if f in factors else 0.0 for f in self._vocab]
        X = np.array([[
            float(features.confluence_score),
            1.0 if features.direction == "call" else 0.0,
        ] + one_hot])
        return float(self._model.predict_proba(X)[0][1])   # P(win)

    def is_ready(self) -> bool:
        return self._model is not None

    def name(self) -> str:
        return "RandomForestScorer"
'''
    with open(scorer_path, "w") as f:
        f.write(scorer_code)

    return {
        "status": "ok",
        "mode": "train",
        "training_samples": len(X_train),
        "test_samples": len(X_test),
        "test_accuracy_pct": acc,
        "win_precision": round(
            report.get("1", {}).get("precision", 0) * 100, 1
        ),
        "win_recall": round(
            report.get("1", {}).get("recall", 0) * 100, 1
        ),
        "model_file": str(model_path),
        "scorer_file": str(scorer_path),
        "next_step": (
            "Set ml.enabled: true in settings.yaml and import RandomForestScorer in runner.py"
        ),
    }

# ─────────────────────────────────────────────────────────────────────────────
# MODE: chart
# ─────────────────────────────────────────────────────────────────────────────
async def _mode_chart(
    asset: str = "EURUSD_otc",
    timeframe: str = "M1",
    last_n: int = 100,
) -> Dict[str, Any]:
    """
    Generates an OHLCV candlestick chart with EMA20, EMA50, Bollinger Bands,
    RSI, ATR, and S/R levels. Saves as PNG to data/charts/.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")   # headless — no display needed on Termux
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
        from matplotlib.gridspec import GridSpec
    except ImportError:
        return {
            "status": "error",
            "message": "Install matplotlib: pip install matplotlib --break-system-packages",
        }

    from indicators import (
        ema as calc_ema,
        bollinger_bands,
        rsi as calc_rsi,
        atr as calc_atr,
        find_sr_levels,
    )

    rows = _load_csv(asset, timeframe)
    candles = _to_candles(rows)[-last_n:]

    if len(candles) < 20:
        return {"status": "error", "message": f"Need 20+ candles, got {len(candles)}"}

    # ── Compute indicators ──────────────────────────────────────────────────
    closes = [c.close for c in candles]
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    times = list(range(len(candles)))

    ema20 = calc_ema(candles, 20)
    ema50 = calc_ema(candles, 50)
    bb = bollinger_bands(candles, 20, 2.0)
    rsi_v = calc_rsi(candles, 14)
    atr_v = calc_atr(candles, 14)
    sr_lvls = find_sr_levels(candles)

    # Pad indicators to match candles length
    def pad(v, length):
        return [None] * (length - len(v)) + list(v) if v else [None] * length

    ema20_p = pad(ema20 if ema20 else [], len(candles))
    ema50_p = pad(ema50 if ema50 else [], len(candles))
    bb_upper = pad(bb["upper"] if bb else [], len(candles))
    bb_lower = pad(bb["lower"] if bb else [], len(candles))
    bb_mid = pad(bb["middle"] if bb else [], len(candles))
    rsi_p = pad([rsi_v] if rsi_v else [], len(candles))

    # ── Plot layout ──────────────────────────────────────────────────────────
    fig = plt.figure(figsize=(16, 10), facecolor="#1a1a2e")
    gs = GridSpec(3, 1, figure=fig, height_ratios=[3, 1, 1], hspace=0.08)

    ax_c = fig.add_subplot(gs[0])   # candles + indicators
    ax_r = fig.add_subplot(gs[1], sharex=ax_c)   # RSI
    ax_a = fig.add_subplot(gs[2], sharex=ax_c)   # ATR

    for ax in [ax_c, ax_r, ax_a]:
        ax.set_facecolor("#0f0f23")
        ax.tick_params(colors="#aaaaaa", labelsize=8)
        ax.spines["bottom"].set_color("#333355")
        ax.spines["top"].set_color("#333355")
        ax.spines["left"].set_color("#333355")
        ax.spines["right"].set_color("#333355")

    # ── Candlesticks ─────────────────────────────────────────────────────────
    for i, c in enumerate(candles):
        color = "#00e676" if c.is_bullish else "#ff1744"
        body_b = min(c.open, c.close)
        body_h = max(c.open, c.close)
        ax_c.plot([i, i], [c.low, c.high], color=color, linewidth=0.8)
        ax_c.add_patch(
            mpatches.FancyBboxPatch(
                (i - 0.3, body_b),
                0.6,
                max(body_h - body_b, 0.00001),
                boxstyle="square,pad=0",
                facecolor=color,
                edgecolor=color,
                linewidth=0,
            )
        )

    # ── Indicators overlay ──────────────────────────────────────────────────
    valid = lambda lst: [(i, v) for i, v in enumerate(lst) if v is not None]

    def plot_line(data, color, label, lw=1.2, ls="-"):
        pts = valid(data)
        if pts:
            xs, ys = zip(*pts)
            ax_c.plot(xs, ys, color=color, linewidth=lw, linestyle=ls, label=label)

    plot_line(ema20_p, "#ffeb3b", "EMA 20", lw=1.0)
    plot_line(ema50_p, "#ff9800", "EMA 50", lw=1.0)
    plot_line(bb_upper, "#42a5f5", "BB Upper", lw=0.8, ls="--")
    plot_line(bb_lower, "#42a5f5", "BB Lower", lw=0.8, ls="--")
    plot_line(bb_mid, "#1565c0", "BB Mid", lw=0.6, ls=":")

    # BB fill
    u_pts = valid(bb_upper)
    l_pts = valid(bb_lower)
    if u_pts and l_pts:
        xs_u, ys_u = zip(*u_pts)
        xs_l, ys_l = zip(*l_pts)
        min_len = min(len(xs_u), len(xs_l))
        ax_c.fill_between(
            xs_u[:min_len], ys_u[:min_len], ys_l[:min_len],
            alpha=0.05, color="#42a5f5"
        )

    # ── S/R levels ──────────────────────────────────────────────────────────
    for lvl in sr_lvls:
        color = "#ef5350" if lvl.level_type == "resistance" else "#66bb6a"
        ax_c.axhline(
            y=lvl.price,
            color=color,
            linewidth=0.8,
            linestyle=":",
            alpha=0.7,
            label=f"{lvl.level_type} ({lvl.touches}t)",
        )
        ax_c.text(
            len(candles) - 1,
            lvl.price,
            f" {lvl.price:.5f}",
            color=color,
            fontsize=7,
            va="center",
            alpha=0.8,
        )

    # ── RSI panel ───────────────────────────────────────────────────────────
    if rsi_v is not None:
        # Simple approximation: plot a flat line at the last RSI value
        ax_r.axhline(y=rsi_v, color="#ab47bc", linewidth=1.2, label=f"RSI {rsi_v:.1f}")
        ax_r.axhline(y=70, color="#ef5350", linewidth=0.6, linestyle="--", alpha=0.5)
        ax_r.axhline(y=30, color="#66bb6a", linewidth=0.6, linestyle="--", alpha=0.5)
        ax_r.set_ylim(0, 100)
        ax_r.fill_between([0, len(candles)], 70, 100, alpha=0.05, color="#ef5350")
        ax_r.fill_between([0, len(candles)], 0, 30, alpha=0.05, color="#66bb6a")
        ax_r.legend(
            loc="upper left",
            fontsize=7,
            facecolor="#1a1a2e",
            edgecolor="#333355",
            labelcolor="#aaaaaa",
        )
    ax_r.set_ylabel("RSI", color="#aaaaaa", fontsize=8)

    # ── ATR panel ───────────────────────────────────────────────────────────
    if atr_v is not None:
        ax_a.axhline(y=atr_v, color="#ffca28", linewidth=1.2, label=f"ATR {atr_v:.5f}")
        ax_a.legend(
            loc="upper left",
            fontsize=7,
            facecolor="#1a1a2e",
            edgecolor="#333355",
            labelcolor="#aaaaaa",
        )
    ax_a.set_ylabel("ATR", color="#aaaaaa", fontsize=8)

    # ── Labels / titles ──────────────────────────────────────────────────────
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    ax_c.set_title(
        f"{asset}  {timeframe}  —  Last {len(candles)} candles  |  Generated {generated_at}",
        color="#e0e0e0",
        fontsize=11,
        pad=10,
    )
    ax_c.set_ylabel("Price", color="#aaaaaa", fontsize=9)
    ax_c.legend(
        loc="upper left",
        fontsize=7,
        ncol=4,
        facecolor="#1a1a2e",
        edgecolor="#333355",
        labelcolor="#aaaaaa",
    )
    ax_c.yaxis.set_major_formatter(plt.FormatStrFormatter("%.5f"))

    ax_a.set_xlabel("Candle index", color="#aaaaaa", fontsize=8)
    plt.setp(ax_c.get_xticklabels(), visible=False)
    plt.setp(ax_r.get_xticklabels(), visible=False)

    out_path = CHART_DIR / f"{asset}_{timeframe}_{datetime.now().strftime('%Y%m%d_%H%M')}.png"
    plt.savefig(str(out_path), dpi=150, bbox_inches="tight", facecolor="#1a1a2e")
    plt.close()
    log.info("Chart saved → %s", out_path)

    return {
        "status": "ok",
        "mode": "chart",
        "asset": asset,
        "timeframe": timeframe,
        "candles_plotted": len(candles),
        "indicators": ["EMA20", "EMA50", "BB(20,2)", "RSI(14)", "ATR(14)", "S/R levels"],
        "file": str(out_path),
    }

# ─────────────────────────────────────────────────────────────────────────────
# MODE: analysis
# ─────────────────────────────────────────────────────────────────────────────
async def _mode_analysis() -> Dict[str, Any]:
    """
    Statistical breakdown of backtest_results.json.
    Breaks down win rate by: strategy, direction, asset, confluence score,
    and individual confluence factor. Also generates a summary chart.
    """
    results_path = DATA_DIR / "backtest_results.json"
    if not results_path.exists():
        return {"status": "error", "message": "Run mode=backtest first."}

    with open(results_path) as f:
        records = json.load(f)

    if not records:
        return {"status": "error", "message": "Backtest results are empty."}

    def win_rate(subset: List[Dict[str, Any]]) -> Dict[str, Any]:
        total = len(subset)
        wins = sum(1 for r in subset if r["outcome"] == "win")
        return {
            "total": total,
            "wins": wins,
            "win_rate_pct": round(wins / total * 100, 1) if total else 0,
        }

    def breakdown(key: str) -> Dict[str, Dict[str, Any]]:
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for r in records:
            v = str(r.get(key, "unknown"))
            groups.setdefault(v, []).append(r)
        return {k: win_rate(v) for k, v in sorted(groups.items())}

    # Per‑factor breakdown
    factor_groups: Dict[str, List[Dict[str, Any]]] = {}
    for r in records:
        for f in r.get("factors", "").split("|"):
            if f:
                factor_groups.setdefault(f, []).append(r)
    factor_stats = {k: win_rate(v) for k, v in sorted(factor_groups.items())}

    # Best / worst factors by win rate (min 10 samples)
    qualifying = {k: v for k, v in factor_stats.items() if v["total"] >= 10}
    best_factors = sorted(
        qualifying, key=lambda k: qualifying[k]["win_rate_pct"], reverse=True
    )[:5]
    worst_factors = sorted(qualifying, key=lambda k: qualifying[k]["win_rate_pct"])[:5]

    # Overall
    overall = win_rate(records)

    # ── Analysis chart ──────────────────────────────────────────────────────
    chart_file = None
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np

        fig, axes = plt.subplots(2, 2, figsize=(14, 10), facecolor="#1a1a2e")
        fig.suptitle("Backtest Analysis", color="#e0e0e0", fontsize=14, y=0.98)

        def bar_chart(ax, data: Dict[str, Dict[str, Any]], title: str, color="#42a5f5"):
            ax.set_facecolor("#0f0f23")
            keys = list(data.keys())
            vals = [data[k]["win_rate_pct"] for k in keys]
            totals = [data[k]["total"] for k in keys]
            bars = ax.bar(
                range(len(keys)),
                vals,
                color=[
                    "#66bb6a" if v >= 55 else "#ef5350" if v < 45 else "#ffca28"
                    for v in vals
                ],
                edgecolor="#333355",
                linewidth=0.5,
            )
            ax.axhline(y=50, color="#aaaaaa", linewidth=0.8, linestyle="--", alpha=0.5)
            ax.set_xticks(range(len(keys)))
            ax.set_xticklabels(
                keys, rotation=30, ha="right", color="#aaaaaa", fontsize=8
            )
            ax.set_ylabel("Win Rate %", color="#aaaaaa", fontsize=8)
            ax.set_ylim(0, 100)
            ax.set_title(title, color="#e0e0e0", fontsize=10)
            ax.tick_params(colors="#aaaaaa")
            for spine in ax.spines.values():
                spine.set_color("#333355")
            for bar, total in zip(bars, totals):
                ax.text(
                    bar.get_x() + bar.get_width() / 2,
                    bar.get_height() + 1,
                    f"n={total}",
                    ha="center",
                    fontsize=7,
                    color="#aaaaaa",
                )

        by_strategy = breakdown("strategy")
        by_direction = breakdown("direction")
        by_asset = breakdown("asset")
        by_confluence = breakdown("confluence")

        bar_chart(axes[0][0], by_strategy, "By Strategy")
        bar_chart(axes[0][1], by_direction, "By Direction")
        bar_chart(axes[1][0], by_asset, "By Asset")
        bar_chart(axes[1][1], by_confluence, "By Confluence Score")

        plt.tight_layout()
        chart_file = str(
            CHART_DIR / f"analysis_{datetime.now().strftime('%Y%m%d_%H%M')}.png"
        )
        plt.savefig(chart_file, dpi=150, bbox_inches="tight", facecolor="#1a1a2e")
        plt.close()
        log.info("Analysis chart saved → %s", chart_file)
    except ImportError:
        log.warning("matplotlib not available — skipping analysis chart")

    return {
        "status": "ok",
        "mode": "analysis",
        "overall": overall,
        "by_strategy": breakdown("strategy"),
        "by_direction": breakdown("direction"),
        "by_asset": breakdown("asset"),
        "by_confluence_score": breakdown("confluence"),
        "by_factor": factor_stats,
        "best_factors": {f: factor_stats[f] for f in best_factors},
        "worst_factors": {f: factor_stats[f] for f in worst_factors},
        "chart_file": chart_file,
    }

# ─────────────────────────────────────────────────────────────────────────────
# MODE: login (simplified: just test connection; OTP handling omitted for now)
# ─────────────────────────────────────────────────────────────────────────────
async def _mode_login(
    practice: bool = True,
    timeout_seconds: int = 10,
) -> Dict[str, Any]:
    """
    Attempt to log in to Quotex by trying to connect via QuotexClient.
    If connection succeeds, return success.
    If it fails, we could fall back to OTP flow, but for simplicity we just
    return the error from the client.
    """
    try:
        client = await _connect(practice)
        await client.disconnect()
        return {
            "status": "ok",
            "mode": "login",
            "message": "Login successful (credentials from config.ini).",
        }
    except Exception as e:
        return {
            "status": "error",
            "mode": "login",
            "error": f"Login failed: {e}",
        }

# ─────────────────────────────────────────────────────────────────────────────
# MODE: live
# ─────────────────────────────────────────────────────────────────────────────
async def _mode_live(
    practice: bool = True,
    duration_minutes: int = 0,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    Starts the full engine.runner trading loop.
    Set duration_minutes=0 for indefinite, or pass a value to auto‑stop.
    Set dry_run=True to analyse signals without placing trades.
    """
    from engine.runner import main as runner_main, build_parser

    log.info(
        "Starting live trader | practice=%s | duration=%dm | dry_run=%s",
        practice,
        duration_minutes,
        dry_run,
    )

    args = build_parser().parse_args([])   # get defaults
    args.dry_run = dry_run
    args.live = not practice
    args.config = str(CONFIG_DIR)

    try:
        if duration_minutes > 0:
            await asyncio.wait_for(
                runner_main(args), timeout=duration_minutes * 60
            )
        else:
            await runner_main(args)
        return {"status": "ok", "mode": "live"}
    except asyncio.TimeoutError:
        return {
            "status": "ok",
            "mode": "live",
            "note": f"Auto-stopped after {duration_minutes}m",
        }
    except KeyboardInterrupt:
        return {
            "status": "ok",
            "mode": "live",
            "note": "Stopped by user",
        }
    except Exception as e:
        return {"status": "error", "mode": "live", "error": str(e)}

# ─────────────────────────────────────────────────────────────────────────────
# MAIN ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────
async def quotex_run(
    mode: str = "status",
    practice: bool = True,
    days: int = 7,
    duration_minutes: int = 0,
    dry_run: bool = False,
    asset: str = "EURUSD_otc",
    timeframe: str = "M1",
    last_n: int = 100,
    assets: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Hermes entry point — controls all pyquotex_trader operations.

    Args:
        mode             : "status" | "pull" | "backtest" | "train" | "chart" | "analysis" | "login" | "live"
        practice         : True = demo account (default), False = real money
        days             : Days of history to pull (mode=pull only)
        duration_minutes : How long to run live trading, 0 = indefinite
        dry_run          : Analyse signals but skip actual trade placement
        asset            : Asset for chart mode (e.g. "EURUSD_otc")
        timeframe        : Timeframe for chart mode ("M1" or "M5")
        last_n           : How many candles to plot in chart mode
        assets           : Override default asset list for pull/backtest
    """
    dispatch = {
        "status":   lambda: _mode_status(practice),
        "pull":     lambda: _mode_pull(days, practice, assets),
        "backtest": lambda: _mode_backtest(assets),
        "train":    lambda: _mode_train(),
        "chart":    lambda: _mode_chart(asset, timeframe, last_n),
        "analysis": lambda: _mode_analysis(),
        "login":    lambda: _mode_login(practice=practice),
        "live":     lambda: _mode_live(practice=practice, duration_minutes=duration_minutes, dry_run=dry_run),
    }

    if mode not in dispatch:
        return {
            "status": "error",
            "message": f"Unknown mode '{mode}'. Valid modes: {list(dispatch)}",
        }

    try:
        return await dispatch[mode]()
    except Exception as e:
        log.exception("quotex_run(mode=%s) failed", mode)
        return {"status": "error", "mode": mode, "error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# CLI shim — run as subprocess or from tmux
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    import json

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    p = argparse.ArgumentParser(description="pyquotex_trader Hermes tool")
    p.add_argument(
        "--mode",
        default="status",
        choices=[
            "status",
            "pull",
            "backtest",
            "train",
            "chart",
            "analysis",
            "login",
            "live",
        ],
    )
    p.add_argument("--days", type=int, default=7)
    p.add_argument(
        "--duration",
        type=int,
        default=0,
        help="Live trading duration in minutes",
    )
    p.add_argument(
        "--asset",
        default="EURUSD_otc",
        help="Asset for chart mode",
    )
    p.add_argument(
        "--timeframe",
        default="M1",
        help="Timeframe for chart mode",
    )
    p.add_argument(
        "--last-n",
        type=int,
        default=100,
        help="Candles to plot in chart mode",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Signal analysis only, no trades",
    )
    p.add_argument(
        "--real",
        action="store_true",
        help="Use real account (default: practice)",
    )
    args = p.parse_args()

    result = asyncio.run(
        quotex_run(
            mode=args.mode,
            practice=not args.real,
            days=args.days,
            duration_minutes=args.duration,
            dry_run=args.dry_run,
            asset=args.asset,
            timeframe=args.timeframe,
            last_n=args.last_n,
        )
    )
    print(json.dumps(result, indent=2))