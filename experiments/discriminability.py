#!/usr/bin/env python3
"""
Discriminability of K2A vs pHash vs dHash.

Answers the question the tamper table cannot: when a hash reports a large
distance, does that mean "this clip was tampered with" or merely "this is not
the same clip"? A hash that decorrelates under any change produces large
distances too, and that is a worse hash, not a better one.

For each hash we compare two distributions:

  brightness attack  - the same clip, modified   (from results.csv)
  unrelated video    - a different clip entirely (computed here, all 1770 pairs)

and report d' = |m1 - m2| / sqrt((s1^2 + s2^2) / 2).

A high d' means the hash can tell a tampered copy from a different video.
A d' near zero means its "detection" carries no information about what changed.

All three hashes are 64-bit and are computed over the SAME four frames K2A
samples (20/40/60/80 per cent), aggregated identically as the mean of the
per-frame Hamming distances, so the comparison is like for like.

Usage:
  ai-service/.venv/bin/python experiments/discriminability.py
"""

import os
import sys
import csv
import math
import itertools
import statistics as st

import cv2
import imagehash
from PIL import Image

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "ai-service"))

RESULTS_CSV = os.path.join(REPO_ROOT, "experiments", "results.csv")
TEST_VIDEOS = os.path.join(REPO_ROOT, "test_videos")
SAMPLE_POSITIONS = (0.2, 0.4, 0.6, 0.8)


def sample_frames(path):
    """The same four frames compute_video_k2a_hash uses."""
    cap = cv2.VideoCapture(path)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    out = []
    for p in SAMPLE_POSITIONS:
        cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(total * p)))
        ok, frame = cap.read()
        out.append(
            Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)) if ok else None
        )
    cap.release()
    return out


def pair_distance(a, b):
    """Mean per-frame Hamming distance, matching the tamper-table aggregation."""
    d = [abs(x - y) for x, y in zip(a, b) if x is not None and y is not None]
    return sum(d) / len(d) if d else None


def d_prime(m1, s1, m2, s2):
    return abs(m1 - m2) / math.sqrt((s1 * s1 + s2 * s2) / 2)


def main():
    rows = list(csv.DictReader(open(RESULTS_CSV)))
    paths = {}
    for root, _, files in os.walk(TEST_VIDEOS):
        for f in files:
            if f.endswith(".mp4"):
                paths.setdefault(f, os.path.join(root, f))

    names = [r["filename"] for r in rows]
    missing = [n for n in names if n not in paths]
    if missing:
        raise SystemExit(f"clips missing from {TEST_VIDEOS}: {missing[:3]} ...")

    print(f"Hashing {len(names)} clips on {len(SAMPLE_POSITIONS)} frames each...")
    ph, dh = {}, {}
    for n in names:
        frames = sample_frames(paths[n])
        ph[n] = [imagehash.phash(f) if f is not None else None for f in frames]
        dh[n] = [imagehash.dhash(f) if f is not None else None for f in frames]

    def column(c):
        v = [float(r[c]) for r in rows if r.get(c) not in ("", "None", None)]
        return st.mean(v), st.stdev(v)

    def unrelated(table):
        v = [
            pair_distance(table[a], table[b])
            for a, b in itertools.combinations(names, 2)
        ]
        v = [x for x in v if x is not None]
        return st.mean(v), st.stdev(v), min(v), len(v)

    # K2A unrelated pairs come from the stored hashes themselves.
    k2a_hashes = [int(r["k2a_original"], 16) for r in rows]
    k2a_unrel = [
        bin(a ^ b).count("1") for a, b in itertools.combinations(k2a_hashes, 2)
    ]

    stats = {
        "K2A": {
            "benign": column("hamming_reencode"),
            "attack": column("hamming_brightness"),
            "unrel": (
                st.mean(k2a_unrel),
                st.stdev(k2a_unrel),
                min(k2a_unrel),
                len(k2a_unrel),
            ),
        },
        "pHash": {
            "benign": column("phash4_hamming_reencode"),
            "attack": column("phash4_hamming_brightness"),
            "unrel": unrelated(ph),
        },
        "dHash": {
            "benign": column("dhash4_hamming_reencode"),
            "attack": column("dhash4_hamming_brightness"),
            "unrel": unrelated(dh),
        },
    }

    print("\nAll 64-bit, same 4 frames, same aggregation.\n")
    print(
        f"{'':7s} {'benign re-encode':>18s} {'brightness attack':>20s} {'unrelated video':>20s}"
    )
    for h in ("K2A", "pHash", "dHash"):
        s = stats[h]
        print(
            f"  {h:5s} {s['benign'][0]:8.2f} +/-{s['benign'][1]:5.2f}"
            f" {s['attack'][0]:12.2f} +/-{s['attack'][1]:5.2f}"
            f" {s['unrel'][0]:12.2f} +/-{s['unrel'][1]:5.2f}"
        )

    print(
        f"\n  (unrelated pairs: n={stats['K2A']['unrel'][3]}, "
        f"closest K2A pair {stats['K2A']['unrel'][2]:.0f} bits, "
        f"closest pHash pair {stats['pHash']['unrel'][2]:.2f})"
    )

    print("\nd' between 'brightness attack' and 'unrelated video':")
    print("  higher = better at telling a tampered copy from a different clip")
    for h in ("K2A", "pHash", "dHash"):
        s = stats[h]
        d = d_prime(s["attack"][0], s["attack"][1], s["unrel"][0], s["unrel"][1])
        print(f"  {h:5s} d' = {d:6.2f}")


if __name__ == "__main__":
    main()
