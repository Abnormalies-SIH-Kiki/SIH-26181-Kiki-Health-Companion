"""
pipeline.py
------------
Runs the whole rule-based prototype end to end:
  generate dummy data -> baseline -> anomaly detection -> daily summary
  -> trend detection -> risk score -> Kiki insight JSON

The TFLite model training is a SEPARATE, optional script
(train_tflite_model.py) since it's exploratory ("does ML help here?"),
not part of the core pipeline Kiki depends on.
"""

import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent  # telemetry_system/

STEPS = [
    "generate_dummy_data.py",
    "baseline.py",
    "anomaly_detection.py",
    "daily_summary.py",
    "trend_detection.py",
    "risk_score.py",
    "insights.py",
]


def main():
    for step in STEPS:
        print(f"\n=== Running {step} ===")
        result = subprocess.run([sys.executable, str(BASE_DIR / "src" / step)], cwd=str(BASE_DIR))
        if result.returncode != 0:
            print(f"Step {step} failed, stopping.")
            sys.exit(1)
    print("\nPipeline complete. See data/kiki_insights.json for the final structured output.")


if __name__ == "__main__":
    main()
