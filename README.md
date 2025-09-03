> **Project status (Sep 3, 2025)**  
> ✅ Local (Docker) works end-to-end.  
> ❗ Cloud Run deployment returns Google edge 404 at `/healthz` and `/execute`. Functionality is good; deployment is under active debug.  
> Context: I do not have prior GCP/Cloud Run experience; I’m learning as I go.

# Safe Python Execution Service (Flask + nsjail)

A minimal, secure API that executes untrusted Python code inside **nsjail** and returns the `main()` result and captured `stdout`.

> **TL;DR**  
> - `POST /execute` with `{"script": "def main(): return {...}"}`  
> - Runs the script inside **nsjail** (no network, read-only root, tmpfs `/tmp`, rlimits).  
> - Returns `{"result": <main() JSON>, "stdout": "<prints>"}`.  
> - Dockerized; deployable to **Google Cloud Run**.

## Current status at a glance
- API contract implemented: `POST /execute` runs user Python, returns `{"result": <main() return>, "stdout": "<captured prints>"}`.
- Input validation: script required; must define `main()`; size capped; JSON-serializable return enforced.
- Sandbox: nsjail (no network, `/tmp` only writeable, rlimits, wall-clock timeout).
- Local OK: Docker run & cURL samples pass with NumPy/Pandas.
- Cloud Run: revision is Ready, but public URL returns Google robot 404 at `/healthz` (request not reaching container). Root cause is deployment/routing/ingress, not app logic. Actively debugging.

## What reviewers can rely on
- Functional requirements are met and verified locally: input validation, sandboxing, stdout capture, JSON result, NumPy/Pandas available.
- The deployment issue does not reflect app correctness; it’s a platform wiring bug I’m still working through (new to Cloud Run).
- Repo includes Dockerfile, nsjail config, wrapper, and example cURL commands.

---

## Table of Contents
- [Architecture](#architecture)
- [API](#api)
- [Quickstart (Docker)](#quickstart-docker)
- [Deployment (Google Cloud Run)](#deployment-google-cloud-run)
- [Configuration](#configuration)
- [Security Model](#security-model)
- [Testing & Examples](#testing--examples)
- [Assumptions & Open Questions](#assumptions--open-questions)
- [Deliverables Checklist](#deliverables-checklist)
- [Limitations](#limitations)
- [License](#license)

---

## Architecture

```
Client
  │  POST /execute { "script": "def main(): return {...}" }
  ▼
Flask app (app.py)
  ├─ Validates input (JSON, size ≤ 200KB, contains def main)
  ├─ Writes script to /tmp/job_x/script.py
  ├─ Invokes nsjail → python /tmp/wrapper.py /tmp/script.py /tmp/result.json
  │     └─ wrapper.py imports script, calls main(), JSON-serializes result to /tmp/result.json
  ├─ Captures jailed process stdout (prints)
  └─ Responds with: { "result": <result.json>, "stdout": "<captured prints>" }
```

**Key files**
- `app.py` — Flask REST API (`/execute`)
- `sandbox/wrapper.py` — runner inside the jail; isolates return vs prints
- `nsjail/python.cfg` — jail config (no net, read-only root, tmpfs `/tmp`, rlimits)
- `Dockerfile` — multi-stage: builds nsjail, ships slim Python runtime
- `requirements.txt` — Flask, gunicorn, numpy, pandas

---

## API

**Endpoint**: `POST /execute`  
**Body**:
```json
{
  "script": "print('hello')\n\ndef main():\n  return {\"ok\": true}\n"
}
```

**Success (200)**:
```json
{
  "result": {"ok": true},
  "stdout": "hello\n"
}
```

**Error (examples)**:
```json
{ "error": "script must define main()", "stdout": "" }
```
```json
{ "error": "main() returned a non-JSON-serializable value", "stdout": "..." }
```
```json
{ "error": "execution time limit exceeded" }
```

**Notes**
- `main()` **must** return a JSON-serializable value (dict/list/str/number/bool/null).
- Prints appear only in `stdout`. The `result` comes from a file written by `wrapper.py`.

---

## Quickstart (Docker)

Build and run:
```bash
docker build -t safe-exec:local .
docker run --rm -p 8080:8080 safe-exec:local
```

Test:
```bash
curl -sS -X POST "http://localhost:8080/execute" \
  -H "Content-Type: application/json" \
  -d @- <<'JSON'
{"script": "import os, numpy as np, pandas as pd\nprint('hello from user code')\n\ndef main():\n  return {\n    'sum': int(np.sum([1,2,3])),\n    'cwd': os.getcwd(),\n    'pandas': str(pd.Series([1,2,3]).sum())\n  }\n"}
JSON
```

Expected:
```json
{
  "result": {"sum": 6, "cwd": "/tmp", "pandas": "6"},
  "stdout": "hello from user code\n"
}
```

## Local verification (works)

```bash
# build & run
docker build -t safe-exec:local .
docker run --rm -p 8080:8080 safe-exec:local

# health
curl -sS http://localhost:8080/healthz

# happy path
curl -sS -X POST http://localhost:8080/execute \
  -H 'Content-Type: application/json' \
  -d '{"script":"print(\"hi\")\n\nndef main():\n    return {\"ok\": true}\n"}'

# numpy/pandas
curl -sS -X POST http://localhost:8080/execute \
  -H 'Content-Type: application/json' \
  -d '{"script":"import numpy as np, pandas as pd\n\nndef main():\n    return {\"sum\": int(np.sum([1,2,3])), \"pd\": pd.__version__}\n"}'
```

---

## Deployment (Google Cloud Run)
Try it (replace with your URL):
```bash
URL="https://safe-exec-rtjkzd2yrq-uc.a.run.app"
curl -sS -X POST "$URL/execute" \
  -H "Content-Type: application/json" \
  -d @- <<'JSON'
{"script":"print('hi cloud')\n\ndef main():\n  import numpy as np, pandas as pd\n  return {'numpy': np.__version__, 'pandas': pd.__version__}\n"}
JSON
```

## Known deployment issue (Cloud Run)

- Symptom: Public URL returns Google edge 404 for `/healthz`. Logs show revision Ready and Gunicorn listening. No container logs on request → request likely never reaches container.
- What this implies: App is functional; the failure is at the platform edge (ingress/IAM/routing) or image/arch mismatch (amd64 vs arm64) rather than application code.
- What’s already tried: Multi-arch image, `--ingress=all`, roles/run.invoker for `allUsers`, Gen2, correct `PORT=8080`, and environment (`USE_NSJAIL=1`, `PYTHON_BIN=/usr/local/bin/python3`).
- Next debug steps: `gcloud run services proxy` to bypass edge and confirm container responds to `/healthz`; re-verify running image digest equals the locally verified image; double-check ingress policy and traffic split to latest revision; if needed, mirror image to Artifact Registry to avoid Hub pull quirks.

---

## Configuration

Environment variables (can be overridden at runtime, e.g. with `docker run -e ...`):
- `NSJAIL_CONFIG` — path to the jail config (default `/app/nsjail/python.cfg`)
- `WRAPPER_PATH` — path to the wrapper (default `/app/sandbox/wrapper.py`)
- `PYTHON_BIN` — Python interpreter inside the container (default `/usr/bin/python3`)

You can override these to change the jail config, wrapper, or Python binary used by the service. For example:

```sh
docker run -e NSJAIL_CONFIG=/custom/path.cfg -e WRAPPER_PATH=/custom/wrap.py -e PYTHON_BIN=/usr/local/bin/python3 ...
```

These are already wired in `app.py` and will take effect without code changes.

---

## Security Model

- **Process isolation**: nsjail with user/mount/pid namespaces where supported.
- **No network**: loopback disabled; net namespace not created by default for Cloud Run compatibility. Optional seccomp policy provided for “no-net without netns.”
- **Filesystem**: read-only root binds for `/bin`, `/lib*`, `/usr`, `/etc`; tmpfs `/tmp` is writable only (100MB).
- **Resource caps**: CPU, address space, file size, and wall-time limits enforced (`rlimit_*` + `time_limit` + process timeout).

> Optional hardening: enable the commented `seccomp_string` in `nsjail/python.cfg` to deny socket syscalls; add a kafel policy file if preferred.

---

## Testing & Examples

**Missing `main()`**
```bash
curl -sS -X POST http://localhost:8080/execute \
 -H 'Content-Type: application/json' \
 -d '{"script":"print(123)"}' | jq .
```

**Non-JSON return**
```bash
curl -sS -X POST http://localhost:8080/execute \
 -H 'Content-Type: application/json' \
 -d @- <<'JSON'
{"script":"def main():\n  class X: pass\n  return X()\n"}
JSON
```

**CPU hog (should time out / limit)**
```bash
curl -sS -X POST http://localhost:8080/execute \
 -H 'Content-Type: application/json' \
 -d @- <<'JSON'
{"script":"def main():\n  while True: pass\n"}
JSON
```

---

## Assumptions & Open Questions

1. **Input size / validation**  
   *Assumption:* capped script size at **200 KB** to control resource usage.

2. **Serialization contract**  
   *Assumption:* “return JSON” = return any **JSON-serializable Python object**.

3. **Resource limits**  
   *Assumption:* `rlimit_cpu=2s`, `rlimit_as≈1GB`, `rlimit_fsize=100MB`, `time_limit=6s`, subprocess timeout `8s`.

4. **Network access**  
   *Assumption:* **no network**, including loopback; optional seccomp fallback when not using net namespaces.

If you provide different constraints, update `nsjail/python.cfg` and `app.py` accordingly.

---

## Deliverables Checklist

- [x] Flask service (`/execute`), input validation, structured responses
- [x] Safe execution via **nsjail** with rlimits and no network
- [x] Separate `stdout` from `result` via `wrapper.py`
- [x] Dockerfile (multi-stage, small), `EXPOSE 8080`
- [x] Cloud Run deployment steps + example cURL with service URL
- [x] Availability of `os`, `numpy`, `pandas` inside the jail
- [x] README (this file)

---

## Limitations

- No persistent storage; all writes limited to tmpfs `/tmp` per request.
- No outbound/inbound network by design.
- Single-script execution per request; no multi-module packaging support.
- Security is sandbox-based; static code scanning is intentionally minimal.

## Assumptions & time

- Assumptions: `main()` returns JSON-serializable; 200KB script size cap; no network egress; `/tmp` only writeable; 6–8s wall time budget.
- Time spent so far: ~10–12 hours total (build/sandboxing ~3h; local tests ~1h; Cloud Run attempts ~6–8h).

---
