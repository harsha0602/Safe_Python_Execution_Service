# Safe Python Execution Service (Flask + nsjail)

A minimal, secure API that executes untrusted Python code inside **nsjail** and returns the `main()` result and captured `stdout`.

> **TL;DR**  
> - `POST /execute` with `{"script": "def main(): return {...}"}`  
> - Runs the script inside **nsjail** (no network, read-only root, tmpfs `/tmp`, rlimits).  
> - Returns `{"result": <main() JSON>, "stdout": "<prints>"}`.  
> - Dockerized; deployable to **Google Cloud Run**.

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

---

## Deployment (Google Cloud Run)

```bash
# Set your project and region
PROJECT_ID="your-gcp-project-id"
REGION="us-central1"
SERVICE="safe-exec"

# Build & push with Cloud Build
gcloud config set project "$PROJECT_ID"
gcloud builds submit --tag "gcr.io/${PROJECT_ID}/${SERVICE}:v1"

# Deploy (Gen2, public URL)
gcloud run deploy "$SERVICE" \
  --image "gcr.io/${PROJECT_ID}/${SERVICE}:v1" \
  --region "$REGION" \
  --platform managed \
  --allow-unauthenticated \
  --cpu=1 --memory=1Gi \
  --port=8080 \
  --execution-environment=gen2

# Get the URL
gcloud run services describe "$SERVICE" --region "$REGION" \
  --format 'value(status.url)'
```

Try it (replace with your URL):
```bash
URL="https://safe-exec-xxxxxxxxxx-uc.a.run.app"
curl -sS -X POST "$URL/execute" \
  -H "Content-Type: application/json" \
  -d @- <<'JSON'
{"script":"print('hi cloud')\n\ndef main():\n  import numpy as np, pandas as pd\n  return {'numpy': np.__version__, 'pandas': pd.__version__}\n"}
JSON
```

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

---

## License

MIT (or add the license your organization requires).
