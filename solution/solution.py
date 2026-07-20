#!/usr/bin/env python3
"""MIB Doc Challenge entry point: <input_pdf_dir> <output_predictions_path>."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mib_pipeline.pipeline import run


def main():
    if len(sys.argv) != 3:
        raise SystemExit("usage: solution.py <input_pdf_dir> <output_predictions_path>")
    n = run(sys.argv[1], sys.argv[2])
    print(f"Wrote {n} predictions to {sys.argv[2]}")


if __name__ == "__main__":
    main()
