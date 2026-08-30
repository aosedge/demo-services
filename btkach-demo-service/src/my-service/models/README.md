# Phi-2 Offline Weights
Place an offline copy of the `microsoft/phi-2` model files in this directory for deployment without internet.

## Downloading the Model
To download the full Phi-2 model (~5.5GB), run the preparation script:

```bash
cd models
pip install transformers torch safetensors
python download_model.py
```

This will create a `phi2/` directory with all necessary model files. Copy the contents of `phi2/` into this `phi-2/` directory.

## Required Files
After preparation, this directory should contain:
- config.json
- tokenizer.json (and related tokenizer files)
- generation_config.json
- model.safetensors (or pytorch model files)

## Memory Requirements
The full, unquantized Phi-2 model will use approximately **5-6 GB of RAM** when loaded. Make sure your edge device has sufficient memory available.

## Environment Flags
- `PHI2_DISABLE=1` - Skip loading the model (useful for testing without weights)

## Verification
Run the main application to test:
```bash
python my-service/main.py
```
If weights exist, you should see `[phi-2] Model loaded successfully (full precision).`

## Licensing
Review licensing/terms of use for `microsoft/phi-2` before redistribution inside a vehicle edge deployment.


## Verification
Run:
```bash
PHI2_DISABLE=0 PHI2_4BIT=1 python my-service/main.py
```
If weights exist, you should see `Phi-2 model loaded (4bit=True).`

## Licensing
Review licensing/terms of use for `microsoft/phi-2` before redistribution inside a vehicle edge deployment.

