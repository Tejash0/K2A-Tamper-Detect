#!/usr/bin/env python3
"""
K2A robustness experiment (ICNSBT 2026 session-chair revision).

Applies three families of common video manipulation - re-encoding, frame
dropping and geometric transforms - to the same 60 UCF-Crime clips as
run_experiments.py, and records the K2A Hamming distance (plus 4-frame pHash and
dHash for reference) between each original and its manipulated copy.

A copy is "flagged" when its K2A distance exceeds the 8-bit threshold
(verdict content_modified, d > 8, as in the paper). For a
content-preserving manipulation a flag is a false positive; for frame dropping,
which the paper treats as tampering, an unflagged copy is a miss.

Usage:
  ai-service/.venv/bin/python experiments/robustness.py [--workers N] [--limit N]
  ai-service/.venv/bin/python experiments/robustness.py --summary

Writes experiments/robustness_results.csv (one row per clip) and prints a
per-condition summary.
"""

import argparse
import csv
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed

import cv2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from run_experiments import (  # noqa: E402
    CATEGORIES,
    CLIP_MANIFEST,
    REPO_ROOT,
    _ffmpeg,
    dhash_frames,
    mean_frame_hash_distance,
    phash_frames,
)
from app.utils.k2a_hash import compute_video_k2a_hash, k2a_hamming_distance  # noqa: E402

RESULTS_CSV = os.path.join(REPO_ROOT, "experiments", "robustness_results.csv")
THRESHOLD = 8
FFMPEG_TIMEOUT = 1800

X264 = ["-c:v", "libx264", "-preset", "fast", "-an"]

# (name, family, error type when flagged/unflagged, ffmpeg args builder).
# Each builder takes the probed (width, height, fps) and returns the arguments
# placed between the input and the output path.
CONDITIONS = [
    ("crf18", "reencode", "fp", lambda w, h, f: [*X264, "-crf", "18"]),
    ("crf23", "reencode", "fp", lambda w, h, f: [*X264, "-crf", "23"]),
    ("crf28", "reencode", "fp", lambda w, h, f: [*X264, "-crf", "28"]),
    ("crf35", "reencode", "fp", lambda w, h, f: [*X264, "-crf", "35"]),
    (
        "h265_crf28",
        "reencode",
        "fp",
        lambda w, h, f: ["-c:v", "libx265", "-preset", "fast", "-crf", "28", "-an"],
    ),
    (
        "downscale_50",
        "reencode",
        "fp",
        lambda w, h, f: [
            "-vf",
            "scale=trunc(iw/4)*2:trunc(ih/4)*2",
            *X264,
            "-crf",
            "23",
        ],
    ),
    ("drop_1frame", "framedrop", "miss", None),  # built per clip: needs the frame count
    (
        "drop_1pct",
        "framedrop",
        "miss",
        lambda w, h, f: [
            "-vf",
            "select='not(eq(mod(n\\,100)\\,50))',setpts=N/FRAME_RATE/TB",
            *X264,
            "-crf",
            "23",
        ],
    ),
    (
        "drop_10pct",
        "framedrop",
        "miss",
        lambda w, h, f: [
            "-vf",
            "select='not(eq(mod(n\\,10)\\,5))',setpts=N/FRAME_RATE/TB",
            *X264,
            "-crf",
            "23",
        ],
    ),
    (
        "half_fps",
        "framedrop",
        "miss",
        lambda w, h, f: ["-vf", f"fps={f / 2:.6f}", *X264, "-crf", "23"],
    ),
    (
        "crop_5pct",
        "geometric",
        "fp",
        lambda w, h, f: [
            "-vf",
            f"crop=iw*0.9:ih*0.9,scale={w}:{h}",
            *X264,
            "-crf",
            "23",
        ],
    ),
    (
        "rotate_2deg",
        "geometric",
        "fp",
        lambda w, h, f: ["-vf", "rotate=2*PI/180", *X264, "-crf", "23"],
    ),
    (
        "rotate_5deg",
        "geometric",
        "fp",
        lambda w, h, f: ["-vf", "rotate=5*PI/180", *X264, "-crf", "23"],
    ),
    ("hflip", "geometric", "fp", lambda w, h, f: ["-vf", "hflip", *X264, "-crf", "23"]),
    (
        "rescale_50",
        "geometric",
        "fp",
        lambda w, h, f: [
            "-vf",
            f"scale=trunc(iw/4)*2:trunc(ih/4)*2,scale={w}:{h}",
            *X264,
            "-crf",
            "23",
        ],
    ),
]

CSV_FIELDS = ["category", "filename"] + [
    f"{prefix}_{name}"
    for name, *_ in CONDITIONS
    for prefix in ("k2a", "phash4", "dhash4")
]


def probe(path: str) -> tuple[int, int, float, int]:
    cap = cv2.VideoCapture(path)
    try:
        return (
            int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
            int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            cap.get(cv2.CAP_PROP_FPS) or 25.0,
            int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),
        )
    finally:
        cap.release()


def drop_one_frame_args(total: int) -> list[str]:
    """Same manipulation as tamper_frame_delete_x264 in run_experiments.py."""
    skip = int(total * 0.40)
    return [
        "-vf",
        f"select='not(eq(n\\,{skip}))',setpts=N/FRAME_RATE/TB",
        *X264,
        "-crf",
        "23",
    ]


def process_clip(category: str, filename: str) -> dict:
    src = os.path.join(CATEGORIES[category], filename)
    w, h, fps, total = probe(src)
    k2a_orig = compute_video_k2a_hash(src)
    ph_orig = phash_frames(src)
    dh_orig = dhash_frames(src)

    row: dict[str, object] = {"category": category, "filename": filename}
    tmp = tempfile.mkdtemp(prefix="k2a_robust_")
    try:
        for name, _family, _err, build in CONDITIONS:
            args = drop_one_frame_args(total) if build is None else build(w, h, fps)
            dst = os.path.join(tmp, f"{name}.mp4")
            r = subprocess.run(
                [_ffmpeg(), "-y", "-loglevel", "error", "-i", src, *args, dst],
                capture_output=True,
                timeout=FFMPEG_TIMEOUT,
            )
            if r.returncode != 0 or not os.path.exists(dst):
                raise RuntimeError(
                    f"ffmpeg failed for {filename} / {name}: {r.stderr.decode()[-500:]}"
                )
            row[f"k2a_{name}"] = k2a_hamming_distance(
                k2a_orig, compute_video_k2a_hash(dst)
            )
            row[f"phash4_{name}"] = mean_frame_hash_distance(ph_orig, phash_frames(dst))
            row[f"dhash4_{name}"] = mean_frame_hash_distance(dh_orig, dhash_frames(dst))
            os.remove(dst)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return row


def summarise(rows: list[dict]) -> None:
    print(
        f"\n{'condition':<14}{'family':<11}{'mean':>7}{'max':>5}{'flag%':>8}{'err':>6}"
        f"{'pH4':>7}{'dH4':>7}"
    )
    for name, family, err, _ in CONDITIONS:
        d = [r[f"k2a_{name}"] for r in rows]
        flagged = 100 * sum(x > THRESHOLD for x in d) / len(d)
        rate = flagged if err == "fp" else 100 - flagged
        ph = statistics.mean(r[f"phash4_{name}"] for r in rows)
        dh = statistics.mean(r[f"dhash4_{name}"] for r in rows)
        print(
            f"{name:<14}{family:<11}{statistics.mean(d):>7.2f}{max(d):>5}{flagged:>8.1f}"
            f"{err + '=' + format(rate, '.1f'):>11}{ph:>7.2f}{dh:>7.2f}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="K2A robustness experiment")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--limit", type=int, help="only the first N clips (smoke test)")
    parser.add_argument(
        "--summary", action="store_true", help="summarise the existing CSV; encode nothing"
    )
    args = parser.parse_args()

    if args.summary:
        with open(RESULTS_CSV, newline="") as f:
            rows = [
                {k: (v if k in ("category", "filename") else float(v)) for k, v in r.items()}
                for r in csv.DictReader(f)
            ]
        summarise(rows)
        return

    with open(CLIP_MANIFEST, newline="") as f:
        clips = [(r["category"], r["filename"]) for r in csv.DictReader(f)]
    if args.limit:
        clips = clips[: args.limit]

    rows = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(process_clip, c, fn): fn for c, fn in clips}
        for fut in as_completed(futures):
            rows.append(fut.result())
            print(f"  done {len(rows)}/{len(clips)}  {futures[fut]}", flush=True)

    order = {fn: i for i, (_, fn) in enumerate(clips)}
    rows.sort(key=lambda r: order[r["filename"]])
    out = RESULTS_CSV if not args.limit else RESULTS_CSV.replace(".csv", "_smoke.csv")
    with open(out, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nWrote {len(rows)} rows to {out}")
    summarise(rows)


if __name__ == "__main__":
    main()
