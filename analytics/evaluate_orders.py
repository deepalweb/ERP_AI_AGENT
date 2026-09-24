"""Offline CLI: python -m analytics.evaluate_orders --timesfm.

Use the isolated .venv-forecast environment for --timesfm; run the skill's
check_system.py first. No business data is sent to the model provider.
"""

import argparse
import hashlib
import json
import time
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from pathlib import Path

from analytics.outlet_orders import evaluate, promotion_decision


def load_timesfm():
    import numpy as np
    import timesfm
    import torch
    from huggingface_hub import model_info

    torch.set_num_threads(2)
    checkpoint = "google/timesfm-2.5-200m-pytorch"
    revision = model_info(checkpoint).sha
    # Eager CPU inference avoids a compiler/toolchain dependency on Windows.
    model = timesfm.TimesFM_2p5_200M_torch.from_pretrained(
        checkpoint, revision=revision, torch_compile=False
    )
    model.compile(
        timesfm.ForecastConfig(
            max_context=256,
            max_horizon=128,
            per_core_batch_size=1,
            normalize_inputs=True,
            use_continuous_quantile_head=True,
            force_flip_invariance=True,
            infer_is_positive=True,
            fix_quantile_crossing=True,
        )
    )

    def predict(history):
        with torch.inference_mode():
            point, quantiles = model.forecast(
                horizon=7, inputs=[np.array(history[-256:], dtype=np.float32)]
            )
        if point.shape != (1, 7) or quantiles.shape != (1, 7, 10):
            raise ValueError("Unexpected TimesFM output shape")
        # Counts cannot be negative. This explicit clipping is part of the method.
        return np.maximum(point[0], 0).tolist(), np.maximum(quantiles[0][:, [1, 9]], 0).tolist()

    return predict, {
        "checkpoint": checkpoint,
        "revision": revision,
        "torch": torch.__version__,
        "timesfm_package": version("timesfm"),
        "context": 256,
        "batch_size": 1,
        "device": "cpu",
        "threads": 2,
        "torch_compile": False,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="artifacts/forecasting/orders.json")
    parser.add_argument("--output", default="artifacts/forecasting/evaluation.json")
    parser.add_argument("--timesfm", action="store_true")
    args = parser.parse_args()
    data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    model, metadata = load_timesfm() if args.timesfm else (None, None)
    start = time.perf_counter()
    result = evaluate(data["series"], model)
    result["evaluated_at"] = datetime.now(UTC).isoformat()
    result["runtime_seconds"] = time.perf_counter() - start
    result["model"] = metadata
    result["dataset"] = {k: v for k, v in data.items() if k != "series"}
    result["dataset_sha256"] = hashlib.sha256(Path(args.input).read_bytes()).hexdigest()
    today = (datetime.now(UTC) + timedelta(hours=5, minutes=30)).date()
    source_date = datetime.fromisoformat(data["latest_source_local"]).date()
    result["decision"] = promotion_decision(
        result, source_date, today, set(data["series"]) == {str(s) for s in data["store_ids"]}
    )
    Path(args.output).write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": result["decision"],
                "runtime_seconds": result["runtime_seconds"],
                "scores": {
                    s: {m: v["overall"] for m, v in ms.items()}
                    for s, ms in result["metrics"].items()
                },
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
