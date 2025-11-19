import os
import time
import csv
import json
import math
import datetime
from pathlib import Path

# Directories
BASE_DIR = Path(__file__).parent
TEST_DATA_DIR = BASE_DIR / "test-data"
REPORT_DIR = BASE_DIR / "result-reports"
MODELS_DIR = BASE_DIR / "models"
# Name of the local transformers model directory inside MODELS_DIR
MODEL_NAME = os.environ.get("LOCAL_MODEL_NAME", "tinyllama")
LOCAL_MODEL_DIR = MODELS_DIR / MODEL_NAME
REPORT_DIR.mkdir(exist_ok=True)

# Backend configuration
MODEL_RUNNER = os.environ.get("MODEL_RUNNER", "transformers").lower()  # "ollama" (default) or "transformers"

def load_speed_data():
    """Load all test_speed_*.csv files from test-data/"""
    files = {}
    for path in TEST_DATA_DIR.glob("test_speed_*.csv"):
        with open(path, newline="") as f:
            reader = csv.DictReader(f)
            rows = [row for row in reader]
            files[path.name] = rows
    return files


def to_float(val):
    try:
        return float(val)
    except (TypeError, ValueError):
        return float("nan")


def summarize_speed(rows):
    speeds = []
    for row in rows:
        speed = to_float(row.get("speed_kmh"))
        if not math.isnan(speed):
            speeds.append(speed)
    speed_limit = 50.0
    speeding_count = sum(1 for s in speeds if s > speed_limit)
    return {
        "count": len(speeds),
        "avg_speed": sum(speeds) / len(speeds) if speeds else 0,
        "min_speed": min(speeds) if speeds else 0,
        "max_speed": max(speeds) if speeds else 0,
        "speed_limit": speed_limit,
        "speeding_instances": speeding_count,
        "speeding_percentage": (speeding_count / len(speeds)) * 100 if speeds else 0,
        "speed_records": speeds,
    }


def build_prompt(summary):
    speed_records = ", ".join([f"{s:.0f}" for s in summary.get("speed_records", [])])
    speeding_instances = summary.get("speeding_instances", 0)
    speeding_percentage = summary.get("speeding_percentage", 0.0)
    speed_limit = summary.get("speed_limit", 50)
    prompt = (
        f"Input: \"Given the speed sensor records (km/h at 5-second intervals): [{speed_records}]. "
        f"Speed limit is {speed_limit} km/h. "
        f"speeding_instances={speeding_instances} (count of individual measurements exceeding the speed limit). "
        f"speeding_percentage={speeding_percentage:.2f}% (percentage of all valid measurements above the limit). "
        f"Provide a text analysis of driver behaviour, focusing on frequency, severity, consistency of speeding and potential safety risk.\""
        f"Output:"
    )
    return prompt


# ----------------------------
# Transformers backend
# ----------------------------

_transformers_cache = {"tokenizer": None, "model": None, "device": None}


def _load_transformers():
    """Load model using local transformers weights from models/<LOCAL_MODEL_NAME>."""
    try:
        from transformers import AutoTokenizer, AutoModelForCausalLM
        import torch

        print(f"[models][transformers] Loading model and tokenizer from local directory '{LOCAL_MODEL_DIR}'...")
        tokenizer = AutoTokenizer.from_pretrained(
            str(LOCAL_MODEL_DIR), local_files_only=True, trust_remote_code=True
        )
        model = AutoModelForCausalLM.from_pretrained(
            str(LOCAL_MODEL_DIR),
            local_files_only=True,
            trust_remote_code=True,
            torch_dtype=torch.float32,
            low_cpu_mem_usage=True,
        )
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        model = model.to(device)
        model.eval()

        _transformers_cache["tokenizer"] = tokenizer
        _transformers_cache["model"] = model
        _transformers_cache["device"] = device
        print("[models][transformers] Model loaded successfully (full precision).")
    except Exception as e:
        print(f"[models][transformers] Model load failed or not present in '{LOCAL_MODEL_DIR}': {e}")
    return _transformers_cache


def _generate_with_transformers(prompt, max_new_tokens=128):
    """Generate analysis text using local transformers model."""
    cache = _load_transformers()
    tokenizer = cache.get("tokenizer")
    model = cache.get("model")
    device = cache.get("device")
    if tokenizer is None or model is None or device is None:
        return (
            "[Transformers backend not available: place model files under 'models/<LOCAL_MODEL_NAME>/' "
            "(see README) and ensure dependencies are installed.]"
        ), "none"
    import torch

    inputs = tokenizer(prompt, return_tensors="pt").to(device)
    with torch.no_grad():
        output_ids = model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            temperature=0.7,
            top_p=0.9,
            do_sample=True,
            pad_token_id=tokenizer.eos_token_id,
        )
    result = tokenizer.decode(output_ids[0], skip_special_tokens=True)
    return result, str(device)


# ----------------------------
# Ollama backend
# ----------------------------


def _generate_with_ollama(prompt, max_new_tokens=128):
    """Generate analysis text using the Ollama Python client.
    """
    try:
        from ollama import chat
    except ImportError as e:
        msg = (
            "[models][ollama] Python package 'ollama' is not installed. "
            "Install it with 'pip install ollama' to use the Ollama backend."
        )
        print(msg)
        return msg, "ollama"

    try:
        # Use the chat API with a single user message containing our prompt.
        # max_new_tokens maps naturally to `num_predict` in Ollama options.
        response = chat(
            model=MODEL_NAME,
            messages=[
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
            options={
                "temperature": 0.7,
                "top_p": 0.9,
                "num_predict": max_new_tokens,
            },
        )

        # Access the content field in a robust way, supporting both dict-style and
        # attribute-style access depending on the installed ollama client version.
        content = None
        try:
            # Newer versions expose a .message.content attribute
            content = getattr(getattr(response, "message", None), "content", None)
        except Exception:
            content = None
        if not content:
            # Fallback to dict-style access if the response is subscriptable
            try:
                content = response["message"]["content"]
            except Exception:
                content = str(response)

        return content, "ollama"
    except Exception as e:
        print(f"[models][ollama] Error calling Ollama client for model '{MODEL_NAME}': {e}")
        return (
            f"[Ollama backend error: {e}. Ensure Ollama is running and the model "
            f"'{MODEL_NAME}' is available.]"
        ), "ollama"


# ----------------------------
# Unified generation API
# ----------------------------


def generate_analysis(prompt, max_new_tokens=128):
    """Generate analysis using the selected backend.

    Backend is chosen via MODEL_RUNNER env var ("ollama" or "transformers").
    Default is "ollama".
    """
    backend = MODEL_RUNNER

    # Log prompt and backend
    print("[models] Backend:", backend)
    print("[models] Local model directory:", LOCAL_MODEL_DIR)
    print("[models] Ollama model:", MODEL_NAME)
    print("[models] Prompt:", prompt)

    start = time.time()
    if backend == "ollama":
        result, device = _generate_with_ollama(prompt, max_new_tokens=max_new_tokens)
    elif backend == "transformers":
        result, device = _generate_with_transformers(prompt, max_new_tokens=max_new_tokens)
    else:
        print(f"[models] Unknown MODEL_RUNNER='{backend}', falling back to 'ollama'.")
        result, device = _generate_with_ollama(prompt, max_new_tokens=max_new_tokens)
    elapsed = time.time() - start

    print(f"[models] Used device/backend identifier: {device}")
    print(f"[models] Generation took {elapsed:.2f} seconds")
    print("[models] Result:", result)

    return result


def generate_reports():
    files_data = load_speed_data()
    if not files_data:
        print("No test data files found (test_speed_*.csv)")
        return
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    timestamp_str = now_utc.strftime("%Y%m%d_%H%M%S")
    all_reports = {}
    for filename, rows in files_data.items():
        print(f"\n{'=' * 60}\nProcessing: {filename}\n{'=' * 60}")
        summary = summarize_speed(rows)
        prompt = build_prompt(summary)
        analysis = generate_analysis(prompt)
        report = {
            "generated_at": now_utc.isoformat(),
            "source_file": filename,
            "summary": summary,
            "prompt": prompt,
            "analysis": analysis,
        }
        all_reports[filename] = report
    combined_report = {"generated_at": now_utc.isoformat(), "reports": all_reports}
    out_path = REPORT_DIR / f"report_{timestamp_str}.json"
    out_path.write_text(json.dumps(combined_report, indent=2), encoding="utf-8")
    print(f"\n{'=' * 60}\nCombined report written to {out_path}\n{'=' * 60}")


if __name__ == "__main__":
    generate_reports()
