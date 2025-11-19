from pathlib import Path
import os
import sys

from transformers import AutoModelForCausalLM, AutoTokenizer


def main():
    """Download any transformers-supported causal LM and tokenizer into models/<local_name>.

    Usage (examples):
        # Use env vars
        MODEL_ID=phi-2 LOCAL_MODEL_NAME=phi2 python download_model.py

        # Or via CLI args
        python download_model.py phi-2 phi2
    """

    # Prefer CLI args; fall back to environment variables; finally defaults.
    if len(sys.argv) >= 3:
        model_id = sys.argv[1]
        local_name = sys.argv[2]
    else:
        model_id = os.environ.get("MODEL_ID", "TinyLlama/TinyLlama-1.1B-Chat-v1.0")
        local_name = os.environ.get("LOCAL_MODEL_NAME", "tinyllama")

    base_dir = Path(__file__).resolve().parent
    models_dir = base_dir
    output_dir = models_dir / local_name

    # Create output directory if it doesn't exist
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"[download_model] Downloading model and tokenizer from '{model_id}'...")
    print(f"[download_model] Target directory: {output_dir}")

    # Download model and tokenizer
    model = AutoModelForCausalLM.from_pretrained(
        model_id,
        trust_remote_code=True,
        torch_dtype="auto",
    )
    tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)

    # Save to local directory
    model.save_pretrained(output_dir)
    tokenizer.save_pretrained(output_dir)

    print(f"[download_model] Model and tokenizer saved to '{output_dir}/' directory.")


if __name__ == "__main__":
    main()
