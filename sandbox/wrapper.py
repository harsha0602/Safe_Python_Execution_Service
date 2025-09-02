
import sys
import importlib.util
import json
import traceback

def main():
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <script.py> <result.json>", file=sys.stderr)
        sys.exit(1)
    script_path = sys.argv[1]
    result_path = sys.argv[2]
    try:
        # Dynamically import user script as module
        spec = importlib.util.spec_from_file_location("user_script", script_path)
        user_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(user_mod)
        if not hasattr(user_mod, "main") or not callable(user_mod.main):
            raise AttributeError("No callable main() in script")
        result = user_mod.main()
        try:
            json.dumps(result)
        except Exception:
            raise TypeError("Return value from main() is not JSON-serializable")
        to_write = result
    except Exception as e:
        tb = traceback.format_exc()
        print(tb, file=sys.stderr)
        to_write = {"__error__": f"{type(e).__name__}: {e}"}
        try:
            with open(result_path, "w") as f:
                json.dump(to_write, f)
        except Exception:
            pass
        sys.exit(1)
    # Write result to result.json
    try:
        with open(result_path, "w") as f:
            json.dump(to_write, f)
    except Exception as e:
        tb = traceback.format_exc()
        print(tb, file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()


