"""Optional RTX 3090 experiment: Qwen3 sequence-classification head + LoRA.

Prepared, not executed in the authoring CPU-only environment (no HF weights).
No generate() call: one forward pass produces four action logits. Not Jev/RLCD.
Run --help. The dataset comes from experiment.py or session-separated real replay.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import random
import re
import time
from pathlib import Path

FEATURE_NAMES = ("lateral_error_over_5cm", "height_over_5cm", "displacement_over_budget",
                 "visible", "unique_identity", "independent_latched", "local_stop",
                 "effect_budget_available")


def render_input(row: dict) -> str:
    values = row["features"]
    if (len(values) != 8 or any(type(v) not in (int, float) or not math.isfinite(v)
                              for v in values)):
        raise ValueError("Expected eight finite numeric state features")
    state = dict(zip(FEATURE_NAMES, (round(float(v), 6) for v in values), strict=True))
    # Never serialize labels, sample/session IDs, or oracle/model explanations.
    return "Ensure the authorized button is latched. State: " + json.dumps(state, sort_keys=True)


def load_rows(path: Path) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    if not rows:
        raise ValueError(f"Empty dataset: {path}")
    for row in rows:
        render_input(row)
        if (type(row.get("label")) is not int or not 0 <= row["label"] < 4
                or not isinstance(row.get("session_id"), str)
                or not isinstance(row.get("sample_id"), str)):
            raise ValueError("Invalid label or missing session/sample identity")
    return rows


def validate_splits(splits: dict[str, list[dict]]) -> None:
    names = list(splits)
    for index, first in enumerate(names):
        for second in names[index + 1:]:
            for key in ("session_id", "sample_id"):
                if {r[key] for r in splits[first]} & {r[key] for r in splits[second]}:
                    raise ValueError(f"{key} leakage between {first} and {second}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--model", default="Qwen/Qwen3-0.6B")
    parser.add_argument("--revision", required=True, help="Pinned 40-character HF commit SHA")
    parser.add_argument("--allow-download", action="store_true")
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--accumulate", type=int, default=8)
    parser.add_argument("--max-length", type=int, default=256)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.revision):
        parser.error("Pin an actual model commit SHA, not main")
    if min(args.epochs, args.batch_size, args.accumulate, args.max_length) < 1:
        parser.error("Training sizes must be positive")
    # Delayed imports keep input-validation tests independent of optional packages.
    import numpy as np
    import torch
    from peft import LoraConfig, TaskType, get_peft_model
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    from experiment import metrics

    if not torch.cuda.is_available() or not torch.cuda.is_bf16_supported():
        parser.error("This optional recipe requires a CUDA device with BF16 support")
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    splits = {name: load_rows(args.data / f"{name}.jsonl")
              for name in ("train", "calibration", "test")}
    validate_splits(splits)
    args.out.mkdir(parents=True, exist_ok=True)
    common = dict(revision=args.revision, local_files_only=not args.allow_download,
                  trust_remote_code=False)
    tokenizer = AutoTokenizer.from_pretrained(args.model, **common)
    tokenizer.pad_token = tokenizer.eos_token
    model = AutoModelForSequenceClassification.from_pretrained(
        args.model, num_labels=4, torch_dtype=torch.bfloat16, **common,
    ).to("cuda")
    model.config.pad_token_id = tokenizer.pad_token_id
    model.config.use_cache = False
    model.config.id2label = {0: "HOLD", 1: "APPROACH", 2: "PRESS", 3: "VERIFY"}
    model.config.label2id = {value: key for key, value in model.config.id2label.items()}
    model = get_peft_model(model, LoraConfig(
        task_type=TaskType.SEQ_CLS, r=16, lora_alpha=32, lora_dropout=0.05,
        target_modules=["q_proj", "v_proj"], modules_to_save=["score"],
    ))

    def batch(rows: list[dict]):
        encoded = tokenizer([render_input(r) for r in rows], padding=True,
                            truncation=False, return_tensors="pt")
        if encoded.input_ids.shape[1] > args.max_length:
            raise ValueError("Input too long; refusing silent loss of safety/context fields")
        return {k: v.to("cuda") for k, v in encoded.items()}

    optimizer = torch.optim.AdamW((p for p in model.parameters() if p.requires_grad), lr=1e-4)
    model.train()
    start = time.perf_counter()
    for _ in range(args.epochs):
        indices = list(range(len(splits["train"])))
        random.shuffle(indices)
        microbatches = [indices[i:i + args.batch_size]
                        for i in range(0, len(indices), args.batch_size)]
        for begin in range(0, len(microbatches), args.accumulate):
            group = microbatches[begin:begin + args.accumulate]
            total_samples = sum(len(micro) for micro in group)
            optimizer.zero_grad(set_to_none=True)
            for micro in group:
                rows = [splits["train"][i] for i in micro]
                labels = torch.tensor([r["label"] for r in rows], device="cuda")
                loss = model(**batch(rows), labels=labels).loss
                (loss * len(rows) / total_samples).backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
    torch.cuda.synchronize()
    training_s = time.perf_counter() - start
    model.eval()

    def logits_for(rows: list[dict]) -> np.ndarray:
        outputs = []
        with torch.inference_mode():
            for i in range(0, len(rows), args.batch_size):
                outputs.append(model(**batch(rows[i:i + args.batch_size])).logits.float().cpu())
        return torch.cat(outputs).numpy()

    lc = logits_for(splits["calibration"])
    yc = np.asarray([r["label"] for r in splits["calibration"]])
    temperatures = np.geomspace(0.3, 3, 61)
    losses = [torch.nn.functional.cross_entropy(torch.tensor(lc / t),
                                               torch.tensor(yc)).item() for t in temperatures]
    temperature = float(temperatures[int(np.argmin(losses))])
    lt = logits_for(splits["test"])
    yt = np.asarray([r["label"] for r in splits["test"]])
    ps = torch.softmax(torch.tensor(lt / temperature), -1).numpy()
    # Merge LoRA for deployment timing. Labels/training do not enter timed input.
    model = model.merge_and_unload().eval()
    model.save_pretrained(args.out / "model", safe_serialization=True)
    tokenizer.save_pretrained(args.out / "model")
    timings = []
    torch.cuda.reset_peak_memory_stats()
    with torch.inference_mode():
        for i in range(550):
            row = splits["test"][i % len(splits["test"])]
            torch.cuda.synchronize()
            before = time.perf_counter_ns()
            logits = model(**batch([row])).logits.float() / temperature
            _ = torch.softmax(logits, -1).cpu().tolist()
            elapsed_ms = (time.perf_counter_ns() - before) / 1e6
            if i >= 50:
                timings.append(elapsed_ms)
    summary = {
        "model": args.model, "revision": args.revision,
        "task": "4-class sequence head; no token generation; not RLCD",
        "training_s": training_s, "temperature": temperature, "test": metrics(ps, yt),
        "tokenization_transfer_forward_return_ms": dict(zip(
            ("p50", "p95", "p99", "max"),
            map(float, np.percentile(timings, [50, 95, 99, 100])), strict=True)),
        "peak_allocated_bytes": torch.cuda.max_memory_allocated(),
        "gpu": torch.cuda.get_device_name(),
        "dependencies": {name: importlib.metadata.version(name)
                         for name in ("torch", "transformers", "peft", "numpy")},
        "data_sha256": {name: hashlib.sha256((args.data / f"{name}.jsonl").read_bytes()).hexdigest()
                        for name in splits},
        "physical_validation": False,
    }
    (args.out / "report.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
