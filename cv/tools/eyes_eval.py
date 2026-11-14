"""Eye open/closed accuracy of cv.signs.eyes on a labelled face dataset.

    python -m cv.tools.eyes_eval --closed DIR --open DIR [--limit 300]

Built for CEW (Closed Eyes in the Wild, 100x100 faces: ``ClosedFace/`` and
``OpenFace/``). Each face is padded and resized like the live face crop
(``eyes.CROP_PX``) and scored by the same ``FaceScorer``. Reports how often a
face is found at all (the rest would be ``not_testable`` live), accuracy at the
configured ``signs.eyes_closed_threshold``, the threshold that would maximise
balanced accuracy on this set, and the score distribution per class. Only
numbers are written (``cv/eval/eyes/``); the dataset stays out of git.

This scores single frames; live, the estimator also averages over
``eyes_window_s``, which helps with blinks but not with a wrong score.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import numpy as np

from cv.clock import iso_utc
from cv.settings import CV_DIR, DEFAULT_CONFIG, MODELS_DIR, load_settings, resolve

OUT_DIR = CV_DIR / "eval" / "eyes"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Eye open/closed accuracy on labelled faces")
    parser.add_argument("--closed", required=True, help="folder of eyes-closed face images")
    parser.add_argument("--open", required=True, help="folder of eyes-open face images")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--limit", type=int, default=0, help="images per class (0 = all)")
    parser.add_argument("--pad", type=float, default=0.35, help="border added around the face, x size")
    parser.add_argument("--label", default="cew")
    parser.add_argument("--out-dir", default=str(OUT_DIR))
    parser.add_argument("--no-save", action="store_true")
    return parser


def images(folder: str, limit: int) -> list[Path]:
    files = sorted(p for p in Path(folder).iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)
    if not files:
        raise FileNotFoundError(f"No images in {folder}")
    if limit and len(files) > limit:
        files = [files[i] for i in np.linspace(0, len(files) - 1, limit).astype(int)]
    return files


def prepare(image_bgr, pad: float, size: int):
    """Pad a tight face crop with its edge pixels and resize to the live crop size."""
    import cv2

    border = int(round(max(image_bgr.shape[:2]) * pad))
    padded = cv2.copyMakeBorder(image_bgr, border, border, border, border, cv2.BORDER_REPLICATE)
    return cv2.resize(padded, (size, size), interpolation=cv2.INTER_CUBIC)


def metrics(closed: list[float | None], open_: list[float | None], threshold: float) -> dict:
    closed_found = [s for s in closed if s is not None]
    open_found = [s for s in open_ if s is not None]

    def at(th):
        tpr = np.mean([s >= th for s in closed_found]) if closed_found else 0.0
        tnr = np.mean([s < th for s in open_found]) if open_found else 0.0
        return float(tpr), float(tnr)

    tpr, tnr = at(threshold)
    grid = np.round(np.arange(0.05, 0.96, 0.01), 2)
    balanced = [(sum(at(th)) / 2, th) for th in grid]
    best_score, best_th = max(balanced)
    found = len(closed_found) + len(open_found)

    def dist(values):
        return {p: round(float(np.percentile(values, p)), 3) for p in (10, 50, 90)} if values else None

    return {
        "images": {"closed": len(closed), "open": len(open_)},
        "face_found_pct": {"closed": round(100 * len(closed_found) / max(1, len(closed)), 1),
                           "open": round(100 * len(open_found) / max(1, len(open_)), 1)},
        "threshold": threshold,
        "closed_recall_pct": round(100 * tpr, 1),          # closed eyes reported as closed
        "open_recall_pct": round(100 * tnr, 1),            # open eyes reported as open
        "accuracy_pct": round(100 * (tpr * len(closed_found) + tnr * len(open_found)) / max(1, found), 1),
        "balanced_accuracy_pct": round(100 * (tpr + tnr) / 2, 1),
        "best_threshold": float(best_th),
        "best_balanced_accuracy_pct": round(100 * best_score, 1),
        "score_closed": dist(closed_found),
        "score_open": dist(open_found),
    }


def run(args: argparse.Namespace) -> dict:
    import cv2

    from cv.signs.eyes import CROP_PX, FaceScorer

    settings = load_settings(args.config).signs
    scorer = FaceScorer(resolve(settings.eyes_model, MODELS_DIR), settings.eyes_turned_deg,
                        settings.eyes_max_yaw_deg)
    scores: dict[str, list[float | None]] = {"closed": [], "open": []}
    started = time.perf_counter()
    for label, folder in (("closed", args.closed), ("open", args.open)):
        for path in images(folder, args.limit):
            image = cv2.imread(str(path))
            scores[label].append(None if image is None else scorer.closed_score(prepare(image, args.pad, CROP_PX)))
            if not scorer.available:
                raise RuntimeError("Face Landmarker unavailable (mediapipe missing or model download failed)")
    elapsed = time.perf_counter() - started
    total = len(scores["closed"]) + len(scores["open"])
    return {
        "run_at_utc": iso_utc(datetime.now(timezone.utc)),
        "label": args.label,
        "pad": args.pad,
        **metrics(scores["closed"], scores["open"], settings.eyes_closed_threshold),
        "ms_per_image": round(1000 * elapsed / max(1, total), 1),
    }


def print_report(result: dict) -> None:
    print(f"\n=== Eyes: {result['label']} ({result['images']['closed']} closed, "
          f"{result['images']['open']} open, {result['ms_per_image']} ms/image) ===")
    print(f"  face found                 : closed {result['face_found_pct']['closed']} %, "
          f"open {result['face_found_pct']['open']} % (the rest -> not_testable)")
    print(f"  at threshold {result['threshold']:<14}: closed recall {result['closed_recall_pct']} %, "
          f"open recall {result['open_recall_pct']} %, accuracy {result['accuracy_pct']} %")
    print(f"  best threshold on this set : {result['best_threshold']} "
          f"(balanced accuracy {result['best_balanced_accuracy_pct']} %)")
    print(f"  score p10/p50/p90          : closed {result['score_closed']}, open {result['score_open']}")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run(args)
    except (FileNotFoundError, RuntimeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    print_report(result)
    if not args.no_save:
        out = Path(args.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        stamp = result["run_at_utc"].replace(":", "").replace("-", "").split(".")[0]
        path = out / f"{result['label']}-{stamp}.json"
        path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(f"\n  saved {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
