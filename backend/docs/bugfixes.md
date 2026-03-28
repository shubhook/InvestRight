# Bug Fixes & Code Issues Log

This document records every bug found and fixed in the InvestRight codebase.
Each entry explains **what was wrong**, **why it was wrong**, and **what was changed**.

---

## Critical Fixes

### 1. `datetime` import at the bottom of `memory_reader.py`
**File:** `backend/memory/memory_reader.py`
**Severity:** Critical

**What was wrong:**
`from datetime import datetime` was placed at the very end of the file (line 162), after the module-level code executed. `update_trade_result()` used `datetime.now()` at line 63 — before the import line was ever reached. This caused:
```
NameError: name 'datetime' is not defined
```
Every call to `update_trade_result()` would crash, making it impossible to record trade outcomes.

**Why it happened:**
A comment said "Import datetime here to avoid circular imports" — but there was no circular import. This was a misdiagnosis, and the import was placed in the wrong location.

**Fix:**
Moved `from datetime import datetime` to the top of the file with the other imports.

---

### 2. Duplicate `_save_memory()` in `memory_reader.py`
**File:** `backend/memory/memory_reader.py`
**Severity:** Critical

**What was wrong:**
`_save_memory()` was defined in both `memory_store.py` and `memory_reader.py`. The two implementations were nearly identical but in separate scopes. This meant writes from `memory_reader` went through a different code path than writes from `memory_store`, making the codebase harder to maintain and increasing risk of drift between the two implementations.

**Fix:**
Removed the duplicate `_save_memory()` from `memory_reader.py` and replaced it with an import from `memory_store`:
```python
from memory.memory_store import _save_memory
```
There is now a single source of truth for writing the memory file.

---

### 3. Missing `run()` function in `main.py`
**File:** `backend/main.py`, `backend/scheduler.py`
**Severity:** Critical

**What was wrong:**
`scheduler.py` imported and called `run(symbol)` from `main.py`:
```python
from backend.main import run
result = run(symbol)
```
But `main.py` only defined a Flask app and the `/analyze` HTTP endpoint — no `run()` function existed. The scheduler would crash with an `ImportError` on startup.

**Fix:**
Added a `run(symbol: str) -> dict` function to `main.py` that executes the full pipeline (fetch → analyze → pattern → decision → risk) and returns the result dict. The Flask `/analyze` endpoint and the `run()` function now share the same pipeline logic.

---

### 4. Wrong import paths in `scheduler.py`
**File:** `backend/scheduler.py`
**Severity:** Critical

**What was wrong:**
The scheduler used `from backend.main import run`, `from backend.utils.logger import setup_logger`, and `from backend.config import Config`. But `run.sh` runs the scheduler from inside the `backend/` directory (`cd backend && python main.py`), so `backend` is not a package on the Python path. These imports would all fail with `ModuleNotFoundError`.

All other files in `backend/` use direct imports (e.g., `from utils.logger import setup_logger`), consistent with being run from within the `backend/` directory.

**Fix:**
Changed all three imports to use direct, un-prefixed module names:
```python
from main import run
from utils.logger import setup_logger
from config import Config
```

---

## High Severity Fixes

### 5. Duplicate log handlers in `logger.py`
**File:** `backend/utils/logger.py`
**Severity:** High

**What was wrong:**
Every call to `setup_logger(name)` unconditionally added a new `StreamHandler` to the logger. Python's `logging.getLogger(name)` returns the **same** logger object for the same name across calls. So each time a module was imported or re-used, a new handler was stacked on top — causing log messages to print 2×, 4×, 8× depending on how many times `setup_logger` was called.

**Fix:**
Added an early-return guard:
```python
if logger.handlers:
    return logger
```
The handler is only added the first time a logger with that name is configured.

---

### 6. Silent exception swallowing in `news_service.py`
**File:** `backend/services/news_service.py`
**Severity:** High

**What was wrong:**
A single bare `except Exception` caught everything and returned an empty list with only a generic error log. It was impossible to distinguish between:
- A network error (DNS failure, timeout, HTTP error)
- Successfully fetching a feed that had zero entries

Both looked identical from the outside: `[]` with a log line. Debugging was difficult because a persistent network outage looked the same as "no news today."

**Fix:**
Split the exception handling into two branches:
- `except (URLError, ConnectionError, TimeoutError)` — logs as a network error
- `except Exception` — logs as an unexpected error

Both still return `[]` as a safe fallback, but the log now clearly identifies the failure type.

---

### 7. Flask `debug=True` hardcoded in production
**File:** `backend/main.py`
**Severity:** High

**What was wrong:**
```python
app.run(debug=True, ...)
```
Flask's debug mode enables the interactive debugger and auto-reloader, and — critically — **exposes full Python stack traces in HTTP error responses**. This leaks internal file paths, variable values, and code structure to anyone who triggers an error.

**Fix:**
Debug mode is now controlled by an environment variable:
```python
debug_mode = os.getenv('FLASK_DEBUG', 'false').lower() == 'true'
app.run(debug=debug_mode, ...)
```
Default is `false`. Set `FLASK_DEBUG=true` in `.env` during local development only.

---

### 8. No input validation on `?symbol=` parameter
**File:** `backend/main.py`
**Severity:** High

**What was wrong:**
The symbol parameter was passed directly to downstream services without any sanitization:
```python
symbol = request.args.get('symbol')
```
This accepted any arbitrary string, including excessively long inputs or characters that could cause issues in URL construction, file paths, or logs.

**Fix:**
Added stripping, uppercasing, and a regex whitelist:
```python
symbol = request.args.get('symbol', '').strip().upper()
if not re.match(r'^[A-Z0-9.\-]{1,20}$', symbol):
    return jsonify({"error": "Invalid symbol format"}), 400
```
Only valid ticker characters (letters, digits, `.`, `-`) up to 20 characters are accepted.

---

### 9. No volume validation before pattern detection
**File:** `backend/utils/pattern_engine.py`
**Severity:** High

**What was wrong:**
Pattern detection functions used `volume` in calculations (e.g., comparing `vol1` vs `vol2` for double-top confirmation) but never checked whether volume data was valid. For zero-volume stocks or weekends/holidays, `volume.sum() == 0` is possible. Accessing `volume.iloc[peak_idx]` on an all-zero series is not itself a crash, but logic that divides by volume (or future extensions) would silently produce incorrect results or crash.

**Fix:**
Added a guard at the start of `detect_pattern()`:
```python
if volume.sum() == 0:
    logger.warning("[PATTERN] All volume values are zero — skipping pattern detection")
    return {"pattern": "none", "confidence": 0.0, "direction": "neutral"}
```

---

### 10. Frontend called a non-existent API endpoint
**Files:** `frontend/script.js`, `frontend/index.html`
**Severity:** High

**What was wrong:**
The frontend JS called `fetch('/api/data')` — an endpoint that has never existed in the backend. The only backend endpoint is `GET /analyze?symbol=`. The frontend would always fail with a 404 and display a confusing "Error" message. The UI was completely non-functional.

Additionally, there was no input field for the user to type a stock symbol — the button had no way to know what to analyze.

**Fix:**
- Added a text `<input id="symbolInput">` to `index.html`
- Updated `script.js` to read the symbol from the input and call the correct endpoint:
  ```js
  fetch(`http://localhost:5001/analyze?symbol=${encodeURIComponent(symbol)}`)
  ```
- Response is now rendered as structured HTML showing decision, confidence, risk levels, and pattern.

---

## Medium Severity Fixes

### 11. Duplicate word in `POSITIVE_WORDS` sentiment list
**File:** `backend/agents/analysis_agent.py`
**Severity:** Medium

**What was wrong:**
`"profit"` appeared twice in the `POSITIVE_WORDS` list:
```python
POSITIVE_WORDS = ["growth", "profit", "upgrade", "beat", "surge", "profit", "earnings", "outperform"]
```
This double-counted any headline containing "profit", skewing the sentiment score upward and making sentiment analysis inconsistent.

**Fix:**
Removed the duplicate entry:
```python
POSITIVE_WORDS = ["growth", "profit", "upgrade", "beat", "surge", "earnings", "outperform"]
```

---

### 12. No warning for sparse data in `data_agent.py`
**File:** `backend/agents/data_agent.py`
**Severity:** Medium

**What was wrong:**
`fetch_and_package_data` returned data without checking if the candle count was sufficient for analysis. Pattern detection requires 30+ candles, but newly listed stocks or illiquid tickers may return far fewer. The pipeline would silently continue, and pattern detection would always return `"none"` without any indication of why.

**Fix:**
Added a warning log when fewer than 30 candles are returned:
```python
if len(ohlc_df) < 30:
    logger.warning(f"[DATA_AGENT] Only {len(ohlc_df)} candles returned for {symbol} — pattern detection requires 30+.")
```
The pipeline continues (no hard failure), but the operator is informed.

---

## Low Severity Fixes

### 13. Port mismatch in `run.sh` and `backend/README.md`
**Files:** `run.sh`, `backend/README.md`
**Severity:** Low

**What was wrong:**
`run.sh` printed "Backend running on port 5000" and `backend/README.md` showed `curl http://localhost:5000/...` — but `main.py` binds to port **5001**. This would confuse anyone following the docs.

**Fix:**
Updated both files to reference port `5001`.

---

## Known Remaining Issues (Not Fixed)

These are tracked for future work but were not in scope for this round:

| Issue | Location | Reason not fixed |
|-------|----------|-----------------|
| `utils/helpers.py` is dead code (`validate_input` always returns `True`, never imported) | `backend/utils/helpers.py` | Removing requires confirming no external callers |
| `backend/app.py` and `models/data_model.py` are empty stubs | Multiple | Placeholder files — deletion is a product decision |
| No authentication or rate limiting on the API | `backend/main.py` | Requires architectural decision on auth strategy |
| Magic numbers (ATR=14, SMA=20/50) hardcoded instead of in config | Multiple | Low risk, refactor when adding more symbols/strategies |
| No test suite despite README referencing one | Entire backend | Tests need to be written from scratch |
| Sentiment analysis is naive keyword matching | `agents/analysis_agent.py` | Would require an NLP model to fix properly |
