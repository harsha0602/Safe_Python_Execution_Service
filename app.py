
import os
import tempfile
import shutil
import json
from flask import Flask, request, jsonify, abort, make_response
import subprocess

app = Flask(__name__)

MAX_SCRIPT_SIZE = 200 * 1024  # 200KB

def get_env(var, default):
    return os.environ.get(var, default)

@app.route("/healthz", methods=["GET"])
def healthz():
    return "ok", 200

@app.route("/execute", methods=["POST"])
def execute():
    # Validate content-type
    if not request.is_json:
        return make_response(jsonify(error="Content-Type must be application/json"), 400)
    data = request.get_json(silent=True)
    if not data or "script" not in data or not isinstance(data["script"], str):
        return make_response(jsonify(error="Missing or invalid 'script' field"), 400)
    script = data["script"]
    if len(script.encode("utf-8")) > MAX_SCRIPT_SIZE:
        return make_response(jsonify(error="Script too large (max 200KB)"), 400)
    if "def main" not in script:
        return make_response(jsonify(error="Script must contain a 'def main' function"), 400)

    # Prepare temp dir and files
    temp_dir = tempfile.mkdtemp(prefix="job_", dir="/tmp")
    script_path = os.path.join(temp_dir, "script.py")
    result_path = os.path.join(temp_dir, "result.json")
    try:
        with open(script_path, "w") as f:
            f.write(script)
        with open(result_path, "w") as f:
            f.write("")

        nsjail_cfg = get_env("NSJAIL_CONFIG", "/app/nsjail/python.cfg")
        wrapper_path = get_env("WRAPPER_PATH", "/app/sandbox/wrapper.py")
        python_bin = get_env("PYTHON_BIN", "/usr/local/bin/python3")

        cmd = [
            "nsjail",
            "--config", nsjail_cfg,
            "--",
            python_bin, wrapper_path, script_path, result_path
        ]

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=8
            )
        except subprocess.TimeoutExpired as e:
            return make_response(jsonify(error="Execution timed out"), 408)

        # Try to read result.json
        try:
            with open(result_path, "r") as f:
                result_content = f.read()
            result_obj = json.loads(result_content) if result_content.strip() else None
        except Exception:
            # Could not parse result, always include both stdout and stderr
            return make_response(jsonify(error="Failed to parse result.json", stdout=proc.stdout, stderr=proc.stderr), 400)

        # Check if result is JSON serializable
        try:
            json.dumps(result_obj)
        except Exception:
            return make_response(jsonify(error="Result is not JSON serializable", stdout=proc.stdout, stderr=proc.stderr), 400)

        return jsonify({"result": result_obj, "stdout": proc.stdout})

    finally:
        try:
            shutil.rmtree(temp_dir)
        except Exception:
            pass

if __name__ == "__main__":
    app.run()
