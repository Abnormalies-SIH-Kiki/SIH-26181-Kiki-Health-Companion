"""
train_tflite_model.py
-----------------------
Trains a tiny (4 -> 4 -> 2 -> 4 -> 4 neuron) dense autoencoder on "normal"
windows of (heart_rate, steps, hour_sin, hour_cos) and converts it to a fully
int8-quantized TFLite model small enough for TFLite Micro on an ESP32.

Why an autoencoder, and why this specific, tiny task?
- The rule-based z-score/persistence detector (anomaly_detection.py) already
  catches univariate HR deviations cheaply, in plain Python/C, with zero ML.
- What it can't easily catch is a *joint* pattern that looks fine on each
  feature alone but is unusual in combination (e.g. HR normal-ish but out of
  sync with activity/time of day in a way a single z-score misses). A tiny
  autoencoder trained on "normal" joint patterns gives a reconstruction-error
  anomaly score that covers that gap -- this is deliberately the smallest
  possible task where a learned model earns its keep over more rules.
- Everything else in this project (baselines, trends, risk score) stays
  rule-based on purpose: seeanomaly rounds/README's "Edge AI investigation"
  section for the reasoning on where NOT to reach for ML.

Output:
  models/anomaly_autoencoder.tflite      (quantized, int8 in/out)
  esp32_export/model_data.h              (C byte array for Arduino/ESP-IDF)
  models/feature_scaler.json             (mean/std used to normalize features,
                                           needed on-device before inference)
"""

import json
import numpy as np
import pandas as pd
import tensorflow as tf
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent  # telemetry_system/
DATA_PATH = str(BASE_DIR / "data" / "telemetry_flagged.csv")
MODEL_DIR = str(BASE_DIR / "models")
EXPORT_DIR = str(BASE_DIR / "esp32_export")


def build_features(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """4 features per sample: heart_rate, steps_in_interval, sin(hour), cos(hour).
    Small, fixed-size feature vector -- exactly what's realistic to compute
    on an ESP32 from a rolling buffer without floating-point-heavy signal
    processing."""
    d = df.copy()
    d["timestamp"] = pd.to_datetime(d["timestamp"])
    hour = d["timestamp"].dt.hour + d["timestamp"].dt.minute / 60.0
    X = np.column_stack([
        d["heart_rate"].to_numpy(dtype=np.float32),
        d["steps_in_interval"].to_numpy(dtype=np.float32),
        np.sin(2 * np.pi * hour / 24).astype(np.float32),
        np.cos(2 * np.pi * hour / 24).astype(np.float32),
    ])
    is_anomaly = d["is_injected_anomaly"].to_numpy() if "is_injected_anomaly" in d.columns else np.zeros(len(d), dtype=bool)
    return X, is_anomaly


def build_autoencoder(input_dim: int = 4) -> tf.keras.Model:
    inputs = tf.keras.Input(shape=(input_dim,))
    x = tf.keras.layers.Dense(4, activation="relu")(inputs)
    x = tf.keras.layers.Dense(2, activation="relu")(x)   # tiny bottleneck
    x = tf.keras.layers.Dense(4, activation="relu")(x)
    outputs = tf.keras.layers.Dense(input_dim, activation="linear")(x)
    model = tf.keras.Model(inputs, outputs)
    model.compile(optimizer="adam", loss="mse")
    return model


def main():
    df = pd.read_csv(DATA_PATH)
    X, is_anomaly = build_features(df)

    # Train ONLY on samples not known to be injected anomalies, so the model
    # learns what "normal" joint HR/steps/time patterns look like.
    X_train_raw = X[~is_anomaly]

    mean = X_train_raw.mean(axis=0)
    std = X_train_raw.std(axis=0)
    std[std == 0] = 1.0
    X_train = (X_train_raw - mean) / std
    X_all = (X - mean) / std

    model = build_autoencoder(input_dim=X.shape[1])
    model.fit(X_train, X_train, epochs=8, batch_size=256, validation_split=0.1, verbose=2)

    # Reconstruction error -> pick a threshold from the normal training
    # distribution (e.g. 99th percentile) as the on-device anomaly cutoff.
    recon_train = model.predict(X_train, verbose=0)
    train_errors = np.mean((recon_train - X_train) ** 2, axis=1)
    threshold = float(np.percentile(train_errors, 99))

    recon_all = model.predict(X_all, verbose=0)
    all_errors = np.mean((recon_all - X_all) ** 2, axis=1)
    pred_anomaly = all_errors > threshold

    tp = int((pred_anomaly & is_anomaly).sum())
    fp = int((pred_anomaly & ~is_anomaly).sum())
    fn = int((~pred_anomaly & is_anomaly).sum())
    print(f"Autoencoder @ threshold={threshold:.4f} -> TP:{tp} FP:{fp} FN:{fn}")

    # --- Convert to quantized TFLite (int8 in/out) for TFLite Micro ---
    def rep_dataset():
        for i in range(0, min(500, len(X_train))):
            yield [X_train[i:i + 1].astype(np.float32)]

    converter = tf.lite.TFLiteConverter.from_keras_model(model)
    converter.optimizations = [tf.lite.Optimize.DEFAULT]
    converter.representative_dataset = rep_dataset
    converter.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS_INT8]
    converter.inference_input_type = tf.int8
    converter.inference_output_type = tf.int8
    tflite_model = converter.convert()

    import os
    os.makedirs(MODEL_DIR, exist_ok=True)
    os.makedirs(EXPORT_DIR, exist_ok=True)

    tflite_path = f"{MODEL_DIR}/anomaly_autoencoder.tflite"
    with open(tflite_path, "wb") as f:
        f.write(tflite_model)
    print(f"Wrote {len(tflite_model)} bytes -> {tflite_path}")

    with open(f"{MODEL_DIR}/feature_scaler.json", "w") as f:
        json.dump({
            "feature_order": ["heart_rate", "steps_in_interval", "hour_sin", "hour_cos"],
            "mean": mean.tolist(),
            "std": std.tolist(),
            "reconstruction_error_threshold": threshold,
        }, f, indent=2)

    # --- Export as a C header (xxd -i style) for Arduino / ESP-IDF ---
    var_name = "g_anomaly_autoencoder_model"
    c_lines = [
        "// Auto-generated from anomaly_autoencoder.tflite. Do not edit by hand.",
        "// Deploy with TensorFlow Lite for Microcontrollers on ESP32.",
        "#ifndef ANOMALY_AUTOENCODER_MODEL_H_",
        "#define ANOMALY_AUTOENCODER_MODEL_H_",
        "",
        f"alignas(8) const unsigned char {var_name}[] = {{",
    ]
    hex_bytes = ", ".join(f"0x{b:02x}" for b in tflite_model)
    # wrap at ~12 bytes per line for readability
    chunks = [hex_bytes.split(", ")[i:i + 12] for i in range(0, len(tflite_model), 12)]
    for chunk in chunks:
        c_lines.append("  " + ", ".join(chunk) + ",")
    c_lines += [
        "};",
        f"const int {var_name}_len = {len(tflite_model)};",
        "",
        "#endif  // ANOMALY_AUTOENCODER_MODEL_H_",
    ]
    header_path = f"{EXPORT_DIR}/model_data.h"
    with open(header_path, "w") as f:
        f.write("\n".join(c_lines))
    print(f"Wrote C header -> {header_path}")


if __name__ == "__main__":
    main()
