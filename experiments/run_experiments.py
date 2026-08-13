#!/usr/bin/env python3
"""
K2A Tamper-Detection Experiment Script
Samples 10 videos per category, computes K2A + pHash + dHash + SHA-256,
creates 4 tampered versions, records Hamming distances to results.csv.

Usage:
  ai-service/.venv/bin/python experiments/run_experiments.py

Expects the 60 evaluated UCF-Crime clips under test_videos/<Category>/, one
directory per entry in CATEGORIES; experiments/clip_manifest.csv lists them.
The full UCF-Crime dataset is not needed - only these 60 clips are evaluated.
"""

import argparse
import sys, os, csv, time, hashlib, shutil, subprocess, tempfile, statistics
import cv2
import numpy as np
import imagehash
from PIL import Image

# ── path setup ────────────────────────────────────────────────────────────────
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "ai-service"))

from app.utils.k2a_hash import (
    compute_video_k2a_hash,
    k2a_hamming_distance,
    K2AHashError,
)

# ── constants ─────────────────────────────────────────────────────────────────
FFMPEG = "/usr/sbin/ffmpeg"
TEST_VIDEOS = os.path.join(REPO_ROOT, "test_videos")
RESULTS_CSV = os.path.join(REPO_ROOT, "experiments", "results.csv")
CLIP_MANIFEST = os.path.join(REPO_ROOT, "experiments", "clip_manifest.csv")
SAMPLES_PER_CATEGORY = 10
TAMPER_TMP = "/tmp/tamper_tmp"
N_WARMUP = 2
N_REPEATS = 5

CATEGORIES = {
    "Normal": os.path.join(TEST_VIDEOS, "Normal_Videos_for_Event_Recognition"),
    "Robbery": os.path.join(TEST_VIDEOS, "Robbery"),
    "RoadAccidents": os.path.join(TEST_VIDEOS, "RoadAccidents"),
    "Shoplifting": os.path.join(TEST_VIDEOS, "Shoplifting"),
    "Stealing": os.path.join(TEST_VIDEOS, "Stealing"),
    "Vandalism": os.path.join(TEST_VIDEOS, "Vandalism"),
}

CSV_FIELDS = [
    "category",
    "filename",
    "file_size_mb",
    "k2a_time_ms",
    "sha256_original",
    "k2a_original",
    "hamming_reencode",
    "hamming_frame_delete",
    "hamming_brightness",
    "hamming_overlay",
    "phash_hamming_reencode",
    "dhash_hamming_reencode",
    "hamming_frame_delete_x264",
    "phash4_hamming_reencode",
    "dhash4_hamming_reencode",
    "phash4_hamming_frame_delete",
    "dhash4_hamming_frame_delete",
    "phash4_hamming_brightness",
    "dhash4_hamming_brightness",
    "phash4_hamming_overlay",
    "dhash4_hamming_overlay",
    "hamming_no_change",
    "hamming_single_byte",
    "sha256_differs_single_byte",
    "k2a_time_ms_std",
    "k2a_time_ms_min",
    "k2a_time_ms_max",
    "k2a_time_repeats",
    # Appended rather than grouped with the other phash4_/dhash4_ columns so that
    # merging them into the published results.csv is a purely additive diff.
    "phash4_hamming_frame_delete_x264",
    "dhash4_hamming_frame_delete_x264",
]


# ── helper: SHA-256 ───────────────────────────────────────────────────────────
def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return "0x" + h.hexdigest()


def timed_k2a_hash(path: str) -> tuple[str, dict]:
    for _ in range(N_WARMUP):
        compute_video_k2a_hash(path)

    hashes = []
    times = []
    for _ in range(N_REPEATS):
        t0 = time.perf_counter()
        h = compute_video_k2a_hash(path)
        times.append((time.perf_counter() - t0) * 1000)
        hashes.append(h)

    first = hashes[0]
    if any(h != first for h in hashes):
        raise RuntimeError(f"K2A timed repeats disagreed for {path}: {hashes}")

    return first, {
        "mean": statistics.mean(times),
        "std": statistics.stdev(times) if len(times) > 1 else 0.0,
        "min": min(times),
        "max": max(times),
        "repeats": len(times),
    }


def flip_payload_byte(src: str, dst: str) -> bool:
    shutil.copyfile(src, dst)
    size = os.path.getsize(dst)
    if size < 1:
        return False
    offset = min(size - 1, int(size * 0.75))
    with open(dst, "r+b") as f:
        f.seek(offset)
        b = f.read(1)
        if not b:
            return False
        f.seek(offset)
        f.write(bytes([b[0] ^ 0xFF]))
    return True


# ── helper: middle-frame pHash / dHash ────────────────────────────────────────
def phash_middle(path: str) -> imagehash.ImageHash | None:
    cap = cv2.VideoCapture(path)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if total < 1:
        cap.release()
        return None
    cap.set(cv2.CAP_PROP_POS_FRAMES, total // 2)
    ret, frame = cap.read()
    cap.release()
    if not ret:
        return None
    pil = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    return imagehash.phash(pil)


def dhash_middle(path: str) -> imagehash.ImageHash | None:
    cap = cv2.VideoCapture(path)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if total < 1:
        cap.release()
        return None
    cap.set(cv2.CAP_PROP_POS_FRAMES, total // 2)
    ret, frame = cap.read()
    cap.release()
    if not ret:
        return None
    pil = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    return imagehash.dhash(pil)


def _hash_frames(path: str, hash_fn) -> list[imagehash.ImageHash | None]:
    cap = cv2.VideoCapture(path)
    try:
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total < 1:
            return [None, None, None, None]

        hashes = []
        for pos in [max(0, int(total * p)) for p in [0.2, 0.4, 0.6, 0.8]]:
            cap.set(cv2.CAP_PROP_POS_FRAMES, pos)
            ret, frame = cap.read()
            if not ret:
                hashes.append(None)
                continue
            pil = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            hashes.append(hash_fn(pil))
        return hashes
    finally:
        cap.release()


def phash_frames(path: str) -> list[imagehash.ImageHash | None]:
    return _hash_frames(path, imagehash.phash)


def dhash_frames(path: str) -> list[imagehash.ImageHash | None]:
    return _hash_frames(path, imagehash.dhash)


def mean_frame_hash_distance(
    original: list[imagehash.ImageHash | None],
    tampered: list[imagehash.ImageHash | None],
) -> float | None:
    distances = [
        int(a - b)
        for a, b in zip(original, tampered)
        if a is not None and b is not None
    ]
    if not distances:
        return None
    return round(statistics.mean(distances), 3)


# ── tamper factories ──────────────────────────────────────────────────────────
def tamper_reencode(src: str, dst: str) -> bool:
    """Re-encode with CRF 28 (lossy, same content)."""
    r = subprocess.run(
        [
            FFMPEG,
            "-y",
            "-i",
            src,
            "-c:v",
            "libx264",
            "-crf",
            "28",
            "-preset",
            "fast",
            "-an",
            dst,
        ],
        capture_output=True,
        timeout=60,
    )
    return r.returncode == 0 and os.path.exists(dst)


def tamper_frame_delete(src: str, dst: str) -> bool:
    """Delete frame at ~40% by writing all other frames with cv2."""
    cap = cv2.VideoCapture(src)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    skip = int(total * 0.40)
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    out = cv2.VideoWriter(dst, fourcc, fps, (w, h))
    idx = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if idx != skip:
            out.write(frame)
        idx += 1
    cap.release()
    out.release()
    return os.path.exists(dst) and os.path.getsize(dst) > 0


def tamper_frame_delete_x264(src: str, dst: str) -> bool:
    """Delete frame at ~40% and encode with ffmpeg/libx264."""
    cap = cv2.VideoCapture(src)
    try:
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    finally:
        cap.release()

    skip = int(total * 0.40)
    r = subprocess.run(
        [
            FFMPEG,
            "-y",
            "-i",
            src,
            "-vf",
            f"select='not(eq(n\\,{skip}))',setpts=N/FRAME_RATE/TB",
            "-c:v",
            "libx264",
            "-crf",
            "23",
            "-preset",
            "fast",
            "-an",
            dst,
        ],
        capture_output=True,
        timeout=60,
    )
    return r.returncode == 0 and os.path.exists(dst)


def tamper_brightness(src: str, dst: str) -> bool:
    """Increase brightness with ffmpeg eq filter."""
    r = subprocess.run(
        [
            FFMPEG,
            "-y",
            "-i",
            src,
            "-vf",
            "eq=brightness=0.3",
            "-c:v",
            "libx264",
            "-crf",
            "23",
            "-preset",
            "fast",
            "-an",
            dst,
        ],
        capture_output=True,
        timeout=60,
    )
    return r.returncode == 0 and os.path.exists(dst)


def tamper_overlay_text(src: str, dst: str) -> bool:
    """Draw 'TAMPERED' text overlay."""
    r = subprocess.run(
        [
            FFMPEG,
            "-y",
            "-i",
            src,
            "-vf",
            "drawtext=text='TAMPERED':fontcolor=red:fontsize=24:x=10:y=10",
            "-c:v",
            "libx264",
            "-crf",
            "23",
            "-preset",
            "fast",
            "-an",
            dst,
        ],
        capture_output=True,
        timeout=60,
    )
    return r.returncode == 0 and os.path.exists(dst)


TAMPERS = [
    ("reencode", tamper_reencode),
    ("frame_delete", tamper_frame_delete),
    ("frame_delete_x264", tamper_frame_delete_x264),
    ("brightness", tamper_brightness),
    ("overlay", tamper_overlay_text),
]


# ── main ──────────────────────────────────────────────────────────────────────
def process_video(category: str, path: str) -> dict | None:
    filename = os.path.basename(path)
    file_size_mb = os.path.getsize(path) / (1024 * 1024)
    print(f"  [{category}] {filename} ({file_size_mb:.1f} MB)", end="", flush=True)

    # K2A hash + timing. compute_video_k2a_hash raises rather than falling back,
    # so a single undecodable clip must not take the whole multi-hour run with it.
    try:
        k2a_orig, timing = timed_k2a_hash(path)
    except K2AHashError as e:
        print(f"  SKIPPED, K2A hash failed: {e}")
        return None

    sha256_orig = sha256_file(path)

    # pHash / dHash on middle frame (baseline comparison)
    ph_orig = phash_middle(path)
    dh_orig = dhash_middle(path)
    ph4_orig = phash_frames(path)
    dh4_orig = dhash_frames(path)

    row = {
        "category": category,
        "filename": filename,
        "file_size_mb": round(file_size_mb, 3),
        "k2a_time_ms": round(timing["mean"], 1),
        "sha256_original": sha256_orig,
        "k2a_original": k2a_orig,
        "hamming_reencode": None,
        "hamming_frame_delete": None,
        "hamming_brightness": None,
        "hamming_overlay": None,
        "phash_hamming_reencode": None,
        "dhash_hamming_reencode": None,
        "hamming_frame_delete_x264": None,
        "phash4_hamming_reencode": None,
        "dhash4_hamming_reencode": None,
        "phash4_hamming_frame_delete": None,
        "dhash4_hamming_frame_delete": None,
        "phash4_hamming_brightness": None,
        "dhash4_hamming_brightness": None,
        "phash4_hamming_overlay": None,
        "dhash4_hamming_overlay": None,
        "hamming_no_change": None,
        "hamming_single_byte": None,
        "sha256_differs_single_byte": None,
        "k2a_time_ms_std": round(timing["std"], 1),
        "k2a_time_ms_min": round(timing["min"], 1),
        "k2a_time_ms_max": round(timing["max"], 1),
        "k2a_time_repeats": timing["repeats"],
        "phash4_hamming_frame_delete_x264": None,
        "dhash4_hamming_frame_delete_x264": None,
    }

    try:
        k2a_no_change = compute_video_k2a_hash(path)
        row["hamming_no_change"] = int(k2a_hamming_distance(k2a_orig, k2a_no_change))
    except K2AHashError as e:
        print(f" [ERR no_change: {e}]", end="", flush=True)

    flipped = os.path.join(
        TAMPER_TMP, f"{os.path.splitext(filename)[0]}_single_byte.mp4"
    )
    try:
        if flip_payload_byte(path, flipped):
            row["sha256_differs_single_byte"] = sha256_file(flipped) != sha256_orig
            try:
                k2a_flipped = compute_video_k2a_hash(flipped)
                row["hamming_single_byte"] = int(
                    k2a_hamming_distance(k2a_orig, k2a_flipped)
                )
            except K2AHashError as e:
                print(
                    f" [NOTE single_byte undecodable after payload byte flip: {e}]",
                    end="",
                    flush=True,
                )
        else:
            print(" [ERR single_byte: could not flip payload byte]", end="", flush=True)
    finally:
        try:
            os.remove(flipped)
        except Exception:
            pass

    # Tampered versions
    for tamper_name, tamper_fn in TAMPERS:
        dst = os.path.join(
            TAMPER_TMP, f"{os.path.splitext(filename)[0]}_{tamper_name}.mp4"
        )
        try:
            ok = tamper_fn(path, dst)
            if ok:
                if tamper_name in {
                    "reencode",
                    "frame_delete",
                    "frame_delete_x264",
                    "brightness",
                    "overlay",
                }:
                    row[f"phash4_hamming_{tamper_name}"] = mean_frame_hash_distance(
                        ph4_orig, phash_frames(dst)
                    )
                    row[f"dhash4_hamming_{tamper_name}"] = mean_frame_hash_distance(
                        dh4_orig, dhash_frames(dst)
                    )

                try:
                    k2a_tampered = compute_video_k2a_hash(dst)
                    hdist = int(k2a_hamming_distance(k2a_orig, k2a_tampered))
                    row[f"hamming_{tamper_name}"] = hdist
                except K2AHashError as e:
                    print(f" [ERR {tamper_name} K2A: {e}]", end="", flush=True)

                if tamper_name == "reencode":
                    ph_t = phash_middle(dst)
                    dh_t = dhash_middle(dst)
                    if ph_orig and ph_t:
                        row["phash_hamming_reencode"] = int(ph_orig - ph_t)
                    if dh_orig and dh_t:
                        row["dhash_hamming_reencode"] = int(dh_orig - dh_t)
            else:
                print(f" [TAMPER FAIL: {tamper_name}]", end="", flush=True)
        except Exception as e:
            print(f" [ERR {tamper_name}: {e}]", end="", flush=True)
        finally:
            try:
                os.remove(dst)
            except Exception:
                pass

    print(
        f" ✓ K2A={timing['mean']:.0f}ms "
        f"re={row['hamming_reencode']} fd={row['hamming_frame_delete']} "
        f"fdx={row['hamming_frame_delete_x264']} br={row['hamming_brightness']} "
        f"ov={row['hamming_overlay']}"
    )
    return row


def expected_row_count() -> int | None:
    """Rows recorded in the committed manifest, or None if it is absent."""
    if not os.path.isfile(CLIP_MANIFEST):
        return None
    with open(CLIP_MANIFEST, newline="") as f:
        return sum(1 for _ in csv.DictReader(f))


def parse_csv_arg(value: str | None) -> list[str] | None:
    if value is None:
        return None
    return [part.strip() for part in value.split(",") if part.strip()]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run K2A tamper-detection measurements."
    )
    parser.add_argument(
        "--clips",
        help="Comma-separated basenames to run, matched across selected categories.",
    )
    parser.add_argument(
        "--categories",
        help="Comma-separated category keys to run.",
    )
    parser.add_argument(
        "--n",
        type=int,
        default=SAMPLES_PER_CATEGORY,
        help=f"Take the first N clips per category, default {SAMPLES_PER_CATEGORY}.",
    )
    parser.add_argument(
        "--out",
        default=RESULTS_CSV,
        help="Output CSV path.",
    )
    args = parser.parse_args()
    args.clips = parse_csv_arg(args.clips)
    args.categories = parse_csv_arg(args.categories)

    if args.n < 0:
        raise SystemExit("--n must be >= 0")

    if args.categories:
        unknown = [c for c in args.categories if c not in CATEGORIES]
        if unknown:
            raise SystemExit(
                "Unknown categories: "
                + ", ".join(unknown)
                + "\nValid categories: "
                + ", ".join(CATEGORIES.keys())
            )

    return args


def main():
    args = parse_args()
    os.makedirs(TAMPER_TMP, exist_ok=True)

    selected_categories = (
        args.categories if args.categories else list(CATEGORIES.keys())
    )
    selected_clip_names = set(args.clips) if args.clips else None

    missing = [
        CATEGORIES[category]
        for category in selected_categories
        if not os.path.isdir(CATEGORIES[category])
    ]
    if missing:
        # Previously this warned and carried on, then overwrote results.csv with
        # a short run. results.csv is the verified ground truth behind every
        # number in the paper, so a partial run must never be able to replace it.
        raise SystemExit(
            "Refusing to run: missing category directories:\n  "
            + "\n  ".join(missing)
            + "\n\nExpected layout is test_videos/<Category>/ (flat, no "
            "Anomaly-Videos-Part-4 wrapper). See experiments/clip_manifest.csv."
        )

    rows = []
    for category in selected_categories:
        folder = CATEGORIES[category]
        videos = sorted(
            [
                os.path.join(folder, f)
                for f in os.listdir(folder)
                if f.lower().endswith((".mp4", ".avi", ".mkv", ".mov"))
            ]
        )
        if selected_clip_names is not None:
            videos = [v for v in videos if os.path.basename(v) in selected_clip_names]
        sample = videos[: args.n]
        print(f"\n=== {category}: {len(sample)} videos ===")
        for v in sample:
            row = process_video(category, v)
            if row:
                rows.append(row)

    expected = expected_row_count()
    default_output = os.path.abspath(args.out) == os.path.abspath(RESULTS_CSV)
    if default_output and expected is not None and len(rows) != expected:
        partial = RESULTS_CSV + ".partial"
        with open(partial, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        raise SystemExit(
            f"Refusing to overwrite {RESULTS_CSV}: produced {len(rows)} rows but "
            f"the manifest expects {expected}. Partial output written to "
            f"{partial} instead. Investigate the skipped clips before rerunning."
        )

    # Write CSV
    with open(args.out, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n✓ Results written to {args.out} ({len(rows)} rows)")

    # Summary stats
    re_dists = [
        r["hamming_reencode"] for r in rows if r["hamming_reencode"] is not None
    ]
    fd_dists = [
        r["hamming_frame_delete"] for r in rows if r["hamming_frame_delete"] is not None
    ]
    br_dists = [
        r["hamming_brightness"] for r in rows if r["hamming_brightness"] is not None
    ]
    ov_dists = [r["hamming_overlay"] for r in rows if r["hamming_overlay"] is not None]
    times = [r["k2a_time_ms"] for r in rows]

    print("\n── Summary ──────────────────────────────────────────────────────")
    if re_dists:
        print(
            f"  Re-encode   Hamming: mean={statistics.mean(re_dists):.2f}  "
            f"min={min(re_dists)}  max={max(re_dists)}"
        )
    if fd_dists:
        print(
            f"  Frame-del   Hamming: mean={statistics.mean(fd_dists):.2f}  "
            f"min={min(fd_dists)}  max={max(fd_dists)}"
        )
    if br_dists:
        print(
            f"  Brightness  Hamming: mean={statistics.mean(br_dists):.2f}  "
            f"min={min(br_dists)}  max={max(br_dists)}"
        )
    if ov_dists:
        print(
            f"  Overlay     Hamming: mean={statistics.mean(ov_dists):.2f}  "
            f"min={min(ov_dists)}  max={max(ov_dists)}"
        )
    if times:
        print(
            f"  K2A time:   mean={statistics.mean(times):.1f} ms  "
            f"min={min(times):.1f}  max={max(times):.1f}"
        )

    # Cleanup temp dir
    shutil.rmtree(TAMPER_TMP, ignore_errors=True)


if __name__ == "__main__":
    main()
