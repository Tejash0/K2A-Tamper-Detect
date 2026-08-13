#!/usr/bin/env python3
"""Generate system architecture diagram → docs/res/fig_architecture.png"""

import os
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(REPO_ROOT, "docs", "res")
os.makedirs(OUT_DIR, exist_ok=True)
OUT = os.path.join(OUT_DIR, "fig_architecture.png")

# ── colour palette ────────────────────────────────────────────────────────────
C = {
    "camera": "#4a90d9",
    "ai": "#7b68ee",
    "backend": "#2e8b57",
    "sqlite": "#cd853f",
    "blockchain": "#c0392b",
    "frontend": "#1a7fa8",
    "arrow": "#333333",
    "label": "#555555",
    "bg": "#f7f9fc",
    "white": "white",
}

fig, ax = plt.subplots(figsize=(12, 7))
ax.set_xlim(0, 12)
ax.set_ylim(0, 7)
ax.set_aspect("equal")
ax.axis("off")
fig.patch.set_facecolor(C["bg"])
ax.set_facecolor(C["bg"])


# ── helper: rounded box ───────────────────────────────────────────────────────
def box(ax, x, y, w, h, color, title, subtitle="", subtitle2=""):
    rect = FancyBboxPatch(
        (x, y),
        w,
        h,
        boxstyle="round,pad=0.08",
        facecolor=color,
        edgecolor="white",
        linewidth=1.5,
        zorder=3,
    )
    ax.add_patch(rect)
    # title
    ty = y + h / 2 + (0.18 if subtitle else 0)
    ax.text(
        x + w / 2,
        ty,
        title,
        ha="center",
        va="center",
        fontsize=9.5,
        fontweight="bold",
        color="white",
        zorder=4,
    )
    if subtitle:
        sy = y + h / 2 - 0.18
        ax.text(
            x + w / 2,
            sy,
            subtitle,
            ha="center",
            va="center",
            fontsize=7.5,
            color="white",
            alpha=0.92,
            zorder=4,
        )
    if subtitle2:
        ax.text(
            x + w / 2,
            y + h / 2 - 0.38,
            subtitle2,
            ha="center",
            va="center",
            fontsize=7.0,
            color="white",
            alpha=0.85,
            zorder=4,
        )


# ── helper: arrow ─────────────────────────────────────────────────────────────
def arrow(ax, x1, y1, x2, y2, label="", color="#333333", style="->", lw=1.6, rad=0.0):
    ax.annotate(
        "",
        xy=(x2, y2),
        xytext=(x1, y1),
        arrowprops=dict(
            arrowstyle=style, color=color, lw=lw, connectionstyle=f"arc3,rad={rad}"
        ),
        zorder=2,
    )
    if label:
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        ax.text(
            mx,
            my + 0.14,
            label,
            ha="center",
            va="bottom",
            fontsize=7,
            color=C["label"],
            zorder=5,
            bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.8),
        )


# ── helper: dashed region ─────────────────────────────────────────────────────
def region(ax, x, y, w, h, label, color):
    rect = FancyBboxPatch(
        (x, y),
        w,
        h,
        boxstyle="round,pad=0.1",
        facecolor=color,
        edgecolor=color,
        linewidth=1.5,
        linestyle="--",
        alpha=0.10,
        zorder=1,
    )
    ax.add_patch(rect)
    ax.text(
        x + 0.12,
        y + h - 0.18,
        label,
        fontsize=7.5,
        color=color,
        alpha=0.75,
        fontstyle="italic",
        zorder=2,
    )


# ═══════════════════════════════════════════════════════════════════
# Layout  (all coordinates in data units)
#
#   Row 1 (y≈5.2): Camera  ──►  AI Service
#   Row 2 (y≈2.8): Frontend  ◄──►  Backend  ──►  SQLite
#                                      │
#   Row 3 (y≈0.5):               Blockchain (EVM)
# ═══════════════════════════════════════════════════════════════════

BW, BH = 2.4, 1.0  # box width / height

# ── region: Edge layer ────────────────────────────────────────────
region(ax, 0.3, 4.8, 5.2, 1.6, "Edge / Capture Layer", C["camera"])

# ── region: Application layer ─────────────────────────────────────
region(ax, 0.3, 2.4, 9.0, 2.1, "Application Layer", C["backend"])

# ── region: Data layer ────────────────────────────────────────────
region(ax, 5.8, 0.2, 5.5, 2.0, "Data & Trust Layer", C["blockchain"])

# ── boxes ─────────────────────────────────────────────────────────
# Camera
box(ax, 0.5, 5.0, BW, BH, C["camera"], "CCTV / Camera", "Simulator", "")

# AI Service
box(
    ax,
    3.3,
    5.0,
    BW + 0.4,
    BH,
    C["ai"],
    "AI Service",
    "FastAPI  :8000",
    "K2A-Hash · SHA-256 · Crime Det.",
)

# Frontend
box(
    ax,
    0.5,
    2.6,
    BW,
    BH,
    C["frontend"],
    "Frontend",
    "React + Vite  :5173",
    "Dashboard · Verifier",
)

# Backend
box(
    ax,
    3.8,
    2.6,
    BW + 0.3,
    BH,
    C["backend"],
    "Backend API",
    "Node.js/Express  :5000",
    "Ethers.js · /record · /verify",
)

# SQLite
box(
    ax,
    7.5,
    2.6,
    BW,
    BH,
    C["sqlite"],
    "SQLite Cache",
    "evidence_log",
    "pending → confirmed",
)

# Blockchain
box(
    ax,
    6.2,
    0.4,
    BW + 0.8,
    BH,
    C["blockchain"],
    "Blockchain Anchor",
    "Hardhat EVM  :8545",
    "EvidenceLog.sol  (bytes32×2)",
)

# ── arrows ────────────────────────────────────────────────────────
# Camera → AI Service
arrow(ax, 0.5 + BW, 5.5, 3.3, 5.5, "video clip")

# AI Service → Backend
arrow(
    ax, 3.3 + BW + 0.4, 5.2, 3.8 + BW + 0.3, 3.6, "videoHash\nperceptualHash", rad=-0.25
)

# Frontend ↔ Backend (double)
arrow(ax, 0.5 + BW, 3.1, 3.8, 3.1, "POST /verify", rad=0.12)
arrow(ax, 3.8, 2.9, 0.5 + BW, 2.9, "verdict + Hamming", rad=0.12)

# Backend → SQLite
arrow(ax, 3.8 + BW + 0.3, 3.1, 7.5, 3.1, "INSERT / UPDATE")

# Backend → Blockchain
arrow(
    ax,
    3.8 + (BW + 0.3) / 2,
    2.6,
    6.2 + (BW + 0.8) / 2,
    0.4 + BH,
    "logEvidence()",
    rad=0.0,
)

# SQLite ↔ Blockchain (dashed verify)
ax.annotate(
    "",
    xy=(8.6, 0.4 + BH),
    xytext=(8.6, 2.6),
    arrowprops=dict(arrowstyle="<->", color=C["sqlite"], lw=1.3, linestyle="dashed"),
    zorder=2,
)
ax.text(
    8.75,
    1.6,
    "Level 1\nvs Level 2",
    ha="left",
    va="center",
    fontsize=6.5,
    color=C["label"],
    bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.8),
)

# ── legend ────────────────────────────────────────────────────────
legend_items = [
    mpatches.Patch(facecolor=C["camera"], label="Camera / Capture"),
    mpatches.Patch(facecolor=C["ai"], label="AI Service (K2A + SHA-256)"),
    mpatches.Patch(facecolor=C["frontend"], label="Frontend (React)"),
    mpatches.Patch(facecolor=C["backend"], label="Backend API (Node.js)"),
    mpatches.Patch(facecolor=C["sqlite"], label="SQLite Cache"),
    mpatches.Patch(facecolor=C["blockchain"], label="Blockchain (Hardhat EVM)"),
]
ax.legend(
    handles=legend_items,
    loc="lower left",
    bbox_to_anchor=(0.0, 0.0),
    fontsize=7.5,
    framealpha=0.9,
    ncol=3,
    columnspacing=0.8,
    handlelength=1.0,
)

ax.set_title(
    "Blockchain-Based CCTV Evidence Verification — System Architecture",
    fontsize=11,
    fontweight="bold",
    pad=10,
    color="#222222",
)

plt.tight_layout()
plt.savefig(OUT, dpi=200, bbox_inches="tight", facecolor=C["bg"])
plt.close()
print(f"✓ Saved: {OUT}")
