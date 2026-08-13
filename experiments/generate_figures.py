#!/usr/bin/env python3
"""
Figure Generation Script for K2A Experiments
Reads experiments/results.csv and saves 4 publication-quality PNG figures
to docs/res/.

Usage:
  /home/moksh/Coding/K2A-Tamper-Detect/ai-service/.venv/bin/python \
    experiments/generate_figures.py
"""

import os, csv, sys
import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.colors import ListedColormap

# -- paths ---------------------------------------------------------------------
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS_CSV = os.environ.get(
    "K2A_RESULTS_CSV",
    os.path.join(REPO_ROOT, "experiments", "results.csv"),
)
OUT_DIR = os.path.join(REPO_ROOT, "docs", "res")
os.makedirs(OUT_DIR, exist_ok=True)

# Publication style
plt.rcParams.update(
    {
        "font.family": "serif",
        "font.size": 11,
        "axes.titlesize": 12,
        "axes.labelsize": 11,
        "xtick.labelsize": 10,
        "ytick.labelsize": 10,
        "legend.fontsize": 10,
        "figure.dpi": 150,
        "savefig.dpi": 200,
        "savefig.bbox": "tight",
        "savefig.pad_inches": 0.05,
    }
)


# -- load CSV ------------------------------------------------------------------
def load_csv(path: str) -> list[dict]:
    rows = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            rows.append(row)
    return rows


def safe_int(v):
    try:
        return int(v) if v not in (None, "", "None") else None
    except (ValueError, TypeError):
        return None


def safe_float(v):
    try:
        return float(v) if v not in (None, "", "None") else None
    except (ValueError, TypeError):
        return None


def safe_bool(v):
    """Parse the CSV's Python-style booleans.

    safe_int/safe_float both return None for 'True'/'False', which would
    silently drop the whole column rather than report it as absent.
    """
    if v in (None, "", "None"):
        return None
    s = str(v).strip().lower()
    if s in ("true", "1"):
        return True
    if s in ("false", "0"):
        return False
    return None


def column_values(rows, column, parser=safe_int):
    vals = [parser(r.get(column)) for r in rows]
    return [x for x in vals if x is not None]


def mean_or_none(vals):
    return float(np.mean(vals)) if vals else None


def annotate_missing_bars(ax, bars, means):
    ymax = ax.get_ylim()[1]
    y = max(0.4, ymax * 0.04)
    for bar, mean in zip(bars, means):
        if mean is None:
            ax.annotate(
                "n/a",
                xy=(bar.get_x() + bar.get_width() / 2, y),
                xytext=(0, 0),
                textcoords="offset points",
                ha="center",
                va="bottom",
                fontsize=8,
                rotation=90,
                color="dimgray",
            )


def bar_heights(means):
    return [0 if mean is None else mean for mean in means]


# -- Figure 1: Hamming distribution histogram ---------------------------------
def fig1_hamming_dist(rows):
    genuine = column_values(rows, "hamming_reencode")
    frame_delete_mp4v = column_values(rows, "hamming_frame_delete")
    frame_delete_x264 = column_values(rows, "hamming_frame_delete_x264")
    overlay = column_values(rows, "hamming_overlay")

    fig, ax = plt.subplots(figsize=(6, 4))

    all_vals = genuine + frame_delete_mp4v + frame_delete_x264 + overlay
    bins = range(0, max(all_vals, default=0) + 3)

    series = [
        (genuine, "Genuine (re-encode)", "steelblue"),
        (frame_delete_mp4v, "Frame delete (mp4v)", "firebrick"),
        (frame_delete_x264, "Frame delete (x264)", "darkorange"),
        (overlay, "Text overlay", "seagreen"),
    ]
    for vals, label, color in series:
        if vals:
            ax.hist(
                vals,
                bins=bins,
                alpha=0.65,
                color=color,
                label=label,
                edgecolor="white",
                linewidth=0.5,
            )

    ax.axvline(
        x=8, color="black", linestyle="--", linewidth=1.5, label="Threshold = 8 bits"
    )

    ax.set_xlabel("Hamming Distance (bits)")
    ax.set_ylabel("Count")
    ax.set_title("K2A Hamming Distance Distribution:\nGenuine vs. Tampered Clips")
    ax.legend()
    ax.grid(axis="y", alpha=0.3)

    out = os.path.join(OUT_DIR, "fig_hamming_dist.png")
    fig.savefig(out)
    plt.close(fig)
    print(f"  ✓ {out}")


# -- Figure 2: Grouped bar - K2A vs pHash vs dHash ----------------------------
def fig2_hash_comparison(rows):
    tamper_types = [
        "reencode",
        "frame_delete",
        "frame_delete_x264",
        "brightness",
        "overlay",
    ]
    labels = [
        "Re-encode",
        "Frame Delete\n(mp4v)",
        "Frame Delete\n(x264)",
        "Brightness",
        "Text Overlay",
    ]

    k2a_means, phash_means, dhash_means = [], [], []
    for t in tamper_types:
        k2a_means.append(mean_or_none(column_values(rows, f"hamming_{t}")))

        # safe_float, not safe_int: these are the MEAN of 4 per-frame
        # distances, so they are routinely fractional (e.g. 0.75, 4.5).
        # Parsing them as ints silently discards those rows and biases the
        # baseline means, which are the whole point of this comparison.
        phash_means.append(
            mean_or_none(column_values(rows, f"phash4_hamming_{t}", safe_float))
        )
        dhash_means.append(
            mean_or_none(column_values(rows, f"dhash4_hamming_{t}", safe_float))
        )

    x = np.arange(len(labels))
    width = 0.26

    fig, ax = plt.subplots(figsize=(7, 4.5))
    bars1 = ax.bar(
        x - width,
        bar_heights(k2a_means),
        width,
        label="K2A-Hash",
        color="steelblue",
        edgecolor="white",
    )
    bars2 = ax.bar(
        x,
        bar_heights(phash_means),
        width,
        label="pHash (4-frame mean)",
        color="darkorange",
        edgecolor="white",
    )
    bars3 = ax.bar(
        x + width,
        bar_heights(dhash_means),
        width,
        label="dHash (4-frame mean)",
        color="seagreen",
        edgecolor="white",
    )

    ax.axhline(y=8, color="black", linestyle="--", linewidth=1.2, label="Threshold = 8")

    ax.set_xlabel("Tamper Type")
    ax.set_ylabel("Mean Hamming Distance (bits)")
    ax.set_title("Mean Hamming Distance by Tamper Type:\nK2A vs. pHash vs. dHash")
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.legend()
    ax.grid(axis="y", alpha=0.3)

    # Value labels on K2A bars
    for bar, mean in zip(bars1, k2a_means):
        if mean is None:
            continue
        h = bar.get_height()
        if h > 0:
            ax.annotate(
                f"{h:.1f}",
                xy=(bar.get_x() + bar.get_width() / 2, h),
                xytext=(0, 2),
                textcoords="offset points",
                ha="center",
                va="bottom",
                fontsize=8,
            )
    annotate_missing_bars(ax, bars1, k2a_means)
    annotate_missing_bars(ax, bars2, phash_means)
    annotate_missing_bars(ax, bars3, dhash_means)

    out = os.path.join(OUT_DIR, "fig_hash_comparison.png")
    fig.savefig(out)
    plt.close(fig)
    print(f"  ✓ {out}")


# -- Figure 3: Detection heatmap ----------------------------------------------
def fig3_detection_matrix(rows):
    THRESHOLD = 8

    tamper_specs = [
        ("reencode", "Re-encode\n(CRF 28)"),
        ("frame_delete", "Frame Delete\n(mp4v)"),
        ("frame_delete_x264", "Frame Delete\n(x264)"),
        ("brightness", "Brightness\n+0.3"),
        ("overlay", "Text\nOverlay"),
    ]

    # K2A: detected if mean Hamming > threshold
    labels, sha256_det, k2a_det = [], [], []
    for t, label in tamper_specs:
        vals = column_values(rows, f"hamming_{t}")
        if vals:
            mean_d = np.mean(vals)
            labels.append(label)
            sha256_det.append(1)
            k2a_det.append(1 if mean_d > THRESHOLD else 0)

    if not labels:
        print("  WARNING: No data for fig3")
        return

    # Matrix: rows=tampers, cols=[SHA-256 detected, K2A detected]
    matrix = np.array([[s, k] for s, k in zip(sha256_det, k2a_det)], dtype=float)

    fig, ax = plt.subplots(figsize=(5, 4))

    cmap = ListedColormap(["#d73027", "#1a9850"])
    im = ax.imshow(matrix, cmap=cmap, vmin=0, vmax=1, aspect="auto")

    ax.set_xticks([0, 1])
    ax.set_xticklabels(["SHA-256\nDetected", "K2A-Hash\nDetected"], fontsize=10)
    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels, fontsize=10)

    for i in range(len(labels)):
        for j in range(2):
            val = matrix[i, j]
            text = "YES" if val == 1 else "NO"
            color = "white"
            ax.text(
                j,
                i,
                text,
                ha="center",
                va="center",
                fontsize=11,
                fontweight="bold",
                color=color,
            )

    green_patch = mpatches.Patch(color="#1a9850", label="Detected")
    red_patch = mpatches.Patch(color="#d73027", label="Not detected")
    ax.legend(
        handles=[green_patch, red_patch], loc="upper right", bbox_to_anchor=(1.45, 1.02)
    )

    ax.set_title("Dual-Hash Detection Matrix\n(SHA-256 vs. K2A-Hash)", fontsize=12)
    ax.set_xlabel("Hash Method")
    ax.set_ylabel("Tamper Type")

    out = os.path.join(OUT_DIR, "fig_detection_matrix.png")
    fig.savefig(out)
    plt.close(fig)
    print(f"  ✓ {out}")


# -- Figure 4: Compute time scatter -------------------------------------------
def fig4_compute_time(rows):
    sizes = [safe_float(r.get("file_size_mb")) for r in rows]
    times = [safe_float(r.get("k2a_time_ms")) for r in rows]
    pairs = [(s, t) for s, t in zip(sizes, times) if s is not None and t is not None]
    if not pairs:
        print("  WARNING: No data for fig4")
        return
    xs, ys = zip(*pairs)

    fig, ax = plt.subplots(figsize=(6, 4))
    ax.scatter(
        xs, ys, color="steelblue", alpha=0.7, s=40, edgecolors="white", linewidths=0.5
    )

    # Linear trend line
    if len(xs) > 2:
        z = np.polyfit(xs, ys, 1)
        p = np.poly1d(z)
        xline = np.linspace(min(xs), max(xs), 100)
        ax.plot(
            xline,
            p(xline),
            "r--",
            linewidth=1.5,
            label=f"Trend (slope={z[0]:.1f} ms/MB)",
        )
        ax.legend()

    ax.axhline(
        y=500, color="orange", linestyle=":", linewidth=1.2, label="500 ms target"
    )

    ax.set_xlabel("File Size (MB)")
    ax.set_ylabel("K2A Compute Time (ms)")
    ax.set_title("K2A-Hash Compute Time vs. File Size")
    ax.grid(alpha=0.3)

    out = os.path.join(OUT_DIR, "fig_compute_time.png")
    fig.savefig(out)
    plt.close(fig)
    print(f"  ✓ {out}")


# -- main ----------------------------------------------------------------------
def main():
    if not os.path.exists(RESULTS_CSV):
        print(f"ERROR: {RESULTS_CSV} not found. Run run_experiments.py first.")
        sys.exit(1)

    rows = load_csv(RESULTS_CSV)
    print(f"Loaded {len(rows)} rows from {RESULTS_CSV}")

    print("\nGenerating figures...")
    fig1_hamming_dist(rows)
    fig2_hash_comparison(rows)
    fig3_detection_matrix(rows)
    fig4_compute_time(rows)

    print(f"\n✓ All 4 figures saved to {OUT_DIR}")

    # Print summary stats for paper
    import statistics

    re_dists = column_values(rows, "hamming_reencode")
    fd_dists = column_values(rows, "hamming_frame_delete")
    fd_x264_dists = column_values(rows, "hamming_frame_delete_x264")
    br_dists = column_values(rows, "hamming_brightness")
    ov_dists = column_values(rows, "hamming_overlay")
    old_phash_re = column_values(rows, "phash_hamming_reencode")
    old_dhash_re = column_values(rows, "dhash_hamming_reencode")
    times = column_values(rows, "k2a_time_ms", safe_float)
    no_change = column_values(rows, "hamming_no_change")
    single_byte = column_values(rows, "hamming_single_byte")
    sha_single_byte = column_values(rows, "sha256_differs_single_byte", safe_bool)
    time_std = column_values(rows, "k2a_time_ms_std", safe_float)
    time_min = column_values(rows, "k2a_time_ms_min", safe_float)
    time_max = column_values(rows, "k2a_time_ms_max", safe_float)
    time_repeats = column_values(rows, "k2a_time_repeats")

    print("\n── Stats for paper ──────────────────────────────────────────────")
    if re_dists:
        print(
            f"  Re-encode   : mean={statistics.mean(re_dists):.2f}  "
            f"stdev={statistics.stdev(re_dists) if len(re_dists) > 1 else 0:.2f}  "
            f"max={max(re_dists)}"
        )
    if fd_dists:
        print(
            f"  Frame-del (mp4v): mean={statistics.mean(fd_dists):.2f}  "
            f"stdev={statistics.stdev(fd_dists) if len(fd_dists) > 1 else 0:.2f}  "
            f"min={min(fd_dists)}"
        )
    if fd_x264_dists:
        print(
            f"  Frame-del (x264): mean={statistics.mean(fd_x264_dists):.2f}  "
            f"stdev={statistics.stdev(fd_x264_dists) if len(fd_x264_dists) > 1 else 0:.2f}  "
            f"min={min(fd_x264_dists)}"
        )
    if br_dists:
        print(
            f"  Brightness  : mean={statistics.mean(br_dists):.2f}  "
            f"stdev={statistics.stdev(br_dists) if len(br_dists) > 1 else 0:.2f}"
        )
    if ov_dists:
        print(
            f"  Overlay     : mean={statistics.mean(ov_dists):.2f}  "
            f"stdev={statistics.stdev(ov_dists) if len(ov_dists) > 1 else 0:.2f}  "
            f"min={min(ov_dists)}"
        )
    if times:
        print(
            f"  K2A time    : mean={statistics.mean(times):.1f} ms  "
            f"min={min(times):.1f}  max={max(times):.1f}"
        )
    if old_phash_re:
        print(
            f"  pHash old re-encode: mean={statistics.mean(old_phash_re):.2f}  "
            f"stdev={statistics.stdev(old_phash_re) if len(old_phash_re) > 1 else 0:.2f}"
        )
    if old_dhash_re:
        print(
            f"  dHash old re-encode: mean={statistics.mean(old_dhash_re):.2f}  "
            f"stdev={statistics.stdev(old_dhash_re) if len(old_dhash_re) > 1 else 0:.2f}"
        )
    for t, label in [
        ("reencode", "Re-encode"),
        ("frame_delete", "Frame-del"),
        ("brightness", "Brightness"),
        ("overlay", "Overlay"),
    ]:
        ph = column_values(rows, f"phash4_hamming_{t}", safe_float)
        dh = column_values(rows, f"dhash4_hamming_{t}", safe_float)
        if ph:
            print(
                f"  pHash4 {label:<10}: mean={statistics.mean(ph):.2f}  "
                f"stdev={statistics.stdev(ph) if len(ph) > 1 else 0:.2f}"
            )
        if dh:
            print(
                f"  dHash4 {label:<10}: mean={statistics.mean(dh):.2f}  "
                f"stdev={statistics.stdev(dh) if len(dh) > 1 else 0:.2f}"
            )
    if no_change:
        print(
            f"  No-change K2A       : mean={statistics.mean(no_change):.2f}  "
            f"max={max(no_change)}"
        )
    if single_byte:
        print(
            f"  Single-byte K2A     : mean={statistics.mean(single_byte):.2f}  "
            f"max={max(single_byte)}"
        )
    if sha_single_byte:
        print(
            f"  Single-byte SHA diff: mean={statistics.mean(sha_single_byte):.2f}  "
            f"detected={sum(1 for x in sha_single_byte if x)} / {len(sha_single_byte)}"
        )
    if time_std:
        print(
            f"  K2A time std        : mean={statistics.mean(time_std):.1f} ms  "
            f"max={max(time_std):.1f}"
        )
    if time_min:
        print(
            f"  K2A time min        : mean={statistics.mean(time_min):.1f} ms  "
            f"min={min(time_min):.1f}"
        )
    if time_max:
        print(
            f"  K2A time max        : mean={statistics.mean(time_max):.1f} ms  "
            f"max={max(time_max):.1f}"
        )
    if time_repeats:
        repeats = sorted(set(time_repeats))
        repeats_s = ", ".join(str(x) for x in repeats)
        print(f"  K2A time repeats    : values={repeats_s}")


if __name__ == "__main__":
    main()
