# Edge Vehicle Telemetry Language Model Demo (Phi-2)

This demo service ingests local CSV telemetry snapshots and generates a health & driving style report using the small `phi-2` language model entirely offline. No external runtime (like Ollama) is required; the Hugging Face `transformers` library loads weights from the local `phi-2/` directory.

## What It Does
1. Loads telemetry CSV files from `test-data/`.
2. Computes summary statistics (avg/min/max) for key metrics.
3. Builds a domain prompt describing current vehicle state.
4. Uses `phi-2` (optionally 4-bit or dynamic int8 quantized) to produce an assessment.
5. Stores a structured JSON report under `result-reports/`.

## Model & Memory
- To stay under 4 GB RAM, the code first attempts a 4-bit load (`load_in_4bit=True` via bitsandbytes). 
- If 4-bit quantization fails (e.g., no GPU / bitsandbytes incompatibility) it will attempt dynamic int8 quantization on Linear layers.
- Set `PHI2_4BIT=0` to skip 4-bit attempt.
- Set `PHI2_INT8_FALLBACK=0` to disable int8 fallback.

## Required Offline Files
Place an offline copy of `microsoft/phi-2` model/tokenizer files in `phi-2/`. See `phi-2/README.md` for details and quantization instructions.

## Environment Flags
| Flag | Default | Purpose |
|------|---------|---------|
| `PHI2_DISABLE` | 0 | Skip loading model entirely (testing). |
| `PHI2_4BIT` | 1 | Attempt 4-bit quantized load first. |
| `PHI2_INT8_FALLBACK` | 1 | Apply dynamic int8 quantization if 4-bit fails. |
| `LM_SEED` | unset | Deterministic sampling seed. |

## Running (Development)
```bash
# Generate one report
PHI2_DISABLE=0 python my-service/main.py
```
For continuous operation, call `main_loop()` instead of single run (modify bottom of `main.py`).

Windows CMD example:
```cmd
set PHI2_DISABLE=0
python my-service\main.py
```

## Venv / Packaging Guidance
Create the venv on the target platform before signing/uploading to ensure native wheels match the edge device.

Linux:
```bash
rm -rf ~/.aos/venv
python3 -m venv ~/.aos/venv
~/.aos/venv/bin/pip install -r btkach-demo-service/requirements.txt
# Place models weights into btkach-demo-service/src/my-service/models/
~/.aos/venv/bin/python -m aos_signer sign
~/.aos/venv/bin/python -m aos_signer upload
```

Windows PowerShell:
```powershell
Remove-Item -Recurse -Force $env:USERPROFILE\.aos\venv
python -m venv $env:USERPROFILE\.aos\venv
$env:USERPROFILE\.aos\venv\Scripts\pip.exe install -r btkach-demo-service\requirements.txt
# Copy phi-2 weights into btkach-demo-service\src\my-service\phi-2\
$env:USERPROFILE\.aos\venv\Scripts\python.exe -m aos_signer sign
$env:USERPROFILE\.aos\venv\Scripts\python.exe -m aos_signer upload
```

## Offline Dependency Vendoring
To package all Python libs inside the `src` zip (so the edge device does not need to download wheels), vendor them:

Linux / macOS:
```bash
python3 -m pip install --upgrade pip
python3 -m pip install --target btkach-demo-service/src/my-service/vendor -r btkach-demo-service/requirements.txt
```
Windows (PowerShell):
```powershell
pip install --upgrade pip
pip install --target btkach-demo-service/src/my-service/vendor -r btkach-demo-service/requirements.txt
```
This creates `my-service/vendor/` with all required packages. The service adds this path to `sys.path` automatically.

Then add phi-2 weights to `my-service/phi-2/` and proceed with signing.

## Minimal Runtime Zip
Ensure the following directories exist inside the zipped `src`:
- `my-service/main.py`
- `my-service/vendor/` (Python dependencies)
- `my-service/phi-2/` (model weights & tokenizer)
- `my-service/test-data/` (CSV telemetry samples)
- `my-service/result-reports/` (created at runtime)

## Report Format
Each JSON report contains:
- `generated_at` (UTC timestamp)
- `summary` (statistics per metric)
- `prompt` (constructed telemetry prompt)
- `analysis` (phi-2 generated assessment or fallback message)

Example filename: `report_20251113_100530.json`.

## Offline Operation
All required runtime dependencies and model weights reside inside the zipped `src` folder; no network access is needed at inference time.

## Extending
Add new telemetry metrics as columns in `test-data/*.csv`; extend the `metrics` dict in `main.py` to summarize them.

## Troubleshooting
- If you see `[Phi-2 weights missing...]`, ensure model files are present in `phi-2/`.
- For CPU-only environments where bitsandbytes fails, confirm dynamic int8 fallback log appears.
- To measure memory usage, run under `python -m memory_profiler my-service/main.py` (after installing `memory_profiler`).
