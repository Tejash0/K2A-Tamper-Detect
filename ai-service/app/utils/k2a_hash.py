"""
K2A-Hash: Diagonal Block Perceptual Hashing for Video Evidence Integrity

Structure:
  - Uniform 8×8 block grid decomposition
  - Literal pixel diagonal extraction per block
  - Bit compression against the absolute constant 128
  - Temporal extension across 4 sampled video frames

Produces 64 significant bits (4 frames × 16 blocks), stored in a 256-bit
bytes32 field. Bits 64-255 are always zero, so the maximum Hamming distance
between any two K2A hashes is 64.

Thresholding against an absolute constant rather than a per-image mean is what
makes K2A sensitive to global intensity change. It is also what collapses
entropy on dark footage: set bits average 22.35 of 64 across the evaluation
set, minimum 1.
"""

import numpy as np
import cv2
from typing import List


class K2AHashError(RuntimeError):
    """Raised when a K2A hash cannot be computed from a video.

    Callers must treat this as a hard failure. Substituting any other digest
    would produce a value that looks like a K2A hash but has none of its
    perceptual properties.
    """


BLOCK_SIZE = 8  # 8×8 pixels per block
GRID_SIZE = 4  # 4×4 grid of blocks = 16 blocks total
RESIZE_DIM = 32  # Resize frame to 32×32 before processing
# Cap on the sequential-read fallback used when frame-count metadata is absent
# or wrong, so a long clip cannot be decoded end to end.
MAX_SEQUENTIAL_SCAN = 600


def _preprocess_frame(frame: np.ndarray) -> np.ndarray:
    """Resize frame to 32×32 grayscale."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if len(frame.shape) == 3 else frame
    return cv2.resize(gray, (RESIZE_DIM, RESIZE_DIM))


def _extract_diagonal(block: np.ndarray) -> List[int]:
    """Extract main diagonal pixel values from 8×8 block (d[0,0]..d[7,7])."""
    return [int(block[i, i]) for i in range(BLOCK_SIZE)]


def _compress_lr(pixels: List[int]) -> int:
    """
    K1 directional compression: push all 1-bits LEFT.
    Returns majority vote bit (1 if more than half the pixels are >= 128).
    """
    ones = sum(1 for p in pixels if p >= 128)
    return 1 if ones > len(pixels) / 2 else 0


def _compress_rl(pixels: List[int]) -> int:
    """
    K2 directional compression: push all 1-bits RIGHT.
    Returns majority vote bit (1 if half or more pixels are >= 128).
    Uses >= threshold for right-bias (asymmetric vs L→R).
    """
    ones = sum(1 for p in pixels if p >= 128)
    return 1 if ones >= len(pixels) / 2 else 0


def _hash_frame(frame_32x32: np.ndarray) -> int:
    """
    Compute 32-bit spatial K2A hash from a 32×32 grayscale frame.
    16 blocks × 2 bits = 32 bits total.
    """
    hash_bits = 0
    for row in range(GRID_SIZE):
        for col in range(GRID_SIZE):
            block = frame_32x32[
                row * BLOCK_SIZE : (row + 1) * BLOCK_SIZE,
                col * BLOCK_SIZE : (col + 1) * BLOCK_SIZE,
            ]
            diag = _extract_diagonal(block)
            h1, h2 = diag[:4], diag[4:]
            k1 = _compress_lr(h1)
            k2 = _compress_rl(h2)
            bit_idx = (row * GRID_SIZE + col) * 2
            hash_bits |= k1 << bit_idx
            hash_bits |= k2 << (bit_idx + 1)
    return hash_bits


def compute_k2a_spatial(frame: np.ndarray) -> str:
    """
    Compute 32-bit spatial K2A hash, padded to 256-bit (bytes32 compatible).
    Returns: 0x-prefixed 64-char hex string.

    NOT USED ON-CHAIN, and not interchangeable with the temporal hash. This
    variant packs its 32 significant bits into the HIGH bytes, whereas
    compute_k2a_temporal packs its 64 into the LOW bytes. Hamming distances
    are therefore not comparable across the two forms, and the backend's
    "bits 64-255 are always zero" assumption holds only for the temporal hash.
    Anything stored or verified must use compute_video_k2a_hash.
    """
    small = _preprocess_frame(frame)
    h32 = _hash_frame(small)
    # Pad to 32 bytes for bytes32 contract compatibility
    h_bytes = h32.to_bytes(4, "big") + b"\x00" * 28
    return "0x" + h_bytes.hex()


def compute_k2a_temporal(frames: List[np.ndarray]) -> str:
    """
    Compute 256-bit temporal K2A hash from multiple frames.

    The diagonal extraction crosses both the spatial domain (pixel position
    within a block) and the temporal domain (frame index), so each block
    contributes bits drawn from more than one time step.

    Measured sensitivity is narrower than that construction suggests: frame
    deletion registers 0.67 bits on average and is classified authentic, and
    deepfake substitution is untested. See the limitations in the README.

    Args:
        frames: List of BGR frames (any size). Up to 4 used.

    Returns:
        0x-prefixed 64-char hex string (bytes32 compatible).

    Raises:
        K2AHashError: If no frames are supplied. Returning an all-zero hash
            here would be indistinguishable from the legitimate hash of a fully
            dark clip, and from the ZeroHash sentinel the backend uses for
            "no perceptual hash".
    """
    if not frames:
        raise K2AHashError("compute_k2a_temporal called with no frames")

    # Sample up to 4 evenly-spaced frames
    n = min(4, len(frames))
    indices = [int(i * (len(frames) - 1) / (n - 1)) for i in range(n)] if n > 1 else [0]
    sampled = [frames[i] for i in indices]

    all_bits = 0
    bit_pos = 0

    for frame_idx, frame in enumerate(sampled):
        small = _preprocess_frame(frame)
        for block_idx in range(GRID_SIZE * GRID_SIZE):
            row = block_idx // GRID_SIZE
            col = block_idx % GRID_SIZE
            block = small[
                row * BLOCK_SIZE : (row + 1) * BLOCK_SIZE,
                col * BLOCK_SIZE : (col + 1) * BLOCK_SIZE,
            ]
            # Temporal diagonal: pixel position wraps by frame index, coupling
            # the spatial block position with time.
            #
            # KNOWN LIMITATION (do not "fix" without re-running the evaluation).
            # frame_idx only ever takes the values 0..3 because exactly 4 frames
            # are sampled, so diag_pos only ever takes the values 0..3 as well.
            # Diagonal positions 4..7 of every block are never read, in any
            # clip. Combined with one pixel per block, the hash observes 16 of
            # the 1024 pixels in each 32x32 frame, or 1.56 per cent.
            #
            # Widening this (sampling 8 frames, or reading the full diagonal)
            # would change every hash and invalidate experiments/results.csv,
            # so it is deliberately left as-is and reported as a limitation.
            diag_pos = frame_idx % BLOCK_SIZE
            diag_val = int(block[diag_pos, diag_pos])
            bit = 1 if diag_val >= 128 else 0
            all_bits |= bit << bit_pos
            bit_pos += 1

    # Encode to 32 bytes (256 bits)
    h_bytes = all_bits.to_bytes(32, "big")
    return "0x" + h_bytes.hex()


def k2a_hamming_distance(hash1: str, hash2: str) -> int:
    """
    Compute Hamming distance between two K2A hashes.
    Returns count of differing bits (0 = identical, higher = more different).
    """
    h1 = int(hash1, 16)
    h2 = int(hash2, 16)
    xor = h1 ^ h2
    return bin(xor).count("1")


# Removed: k2a_complement_verify(). It computed (h ^ ~h) == all-ones, which is a
# tautology true of every integer, so it returned True unconditionally and
# verified nothing. It had no callers. A keyless self-check of this kind is not
# possible: any function of the hash alone can be recomputed by whoever altered
# the hash.


def compute_video_k2a_hash(video_path: str) -> str:
    """
    Main entry point: compute temporal K2A hash from a video file.

    Samples 4 evenly-spaced frames at 20%, 40%, 60%, 80% of the video,
    then builds a 256-bit temporal hash where the diagonal traversal
    crosses both spatial blocks and temporal frames simultaneously.

    Args:
        video_path: Path to video file.

    Returns:
        0x-prefixed 64-char hex string (bytes32 compatible).

    Raises:
        K2AHashError: If the video cannot be opened or no frame can be decoded.
            There is deliberately no fallback. A SHA-256 digest is structurally
            indistinguishable from a K2A hash but carries no perceptual
            information, so returning one would silently anchor a meaningless
            value on-chain and make every later Hamming comparison noise.
    """
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise K2AHashError(f"Cannot open video for K2A hashing: {video_path}")

    try:
        # CAP_PROP_FRAME_COUNT is metadata and is unreliable: several valid
        # containers, and any stream without an index, report 0 or a negative
        # count while decoding perfectly well. Treat it as a hint for seeking
        # only, and let the "decoded nothing" check below be the real failure
        # condition.
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        frames = []

        if total >= 1:
            # Sample 4 frames at 20%, 40%, 60%, 80% through the video
            sample_positions = [max(0, int(total * p)) for p in [0.2, 0.4, 0.6, 0.8]]
            for pos in sample_positions:
                cap.set(cv2.CAP_PROP_POS_FRAMES, pos)
                ret, frame = cap.read()
                if ret:
                    frames.append(frame)

        if not frames:
            # Either the count was unusable or every seek failed. Fall back to
            # reading sequentially from the start, keeping 4 evenly spaced from
            # whatever decodes.
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            decoded = []
            while len(decoded) < MAX_SEQUENTIAL_SCAN:
                ret, frame = cap.read()
                if not ret:
                    break
                decoded.append(frame)
            if decoded:
                n = min(4, len(decoded))
                step = (len(decoded) - 1) / (n - 1) if n > 1 else 0
                frames = [decoded[int(i * step)] for i in range(n)]
    finally:
        cap.release()

    if not frames:
        raise K2AHashError(f"Decoded no frames from video: {video_path}")

    return compute_k2a_temporal(frames)
