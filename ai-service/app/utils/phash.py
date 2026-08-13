"""
Perceptual Hashing for Video Files - backed by K2A-Hash.

K2A-Hash replaces imagehash.phash() as the perceptual hash stored on-chain.
Algorithm: uniform 8x8 block grid + literal pixel diagonal extraction +
bit compression against the absolute constant 128 + temporal extension across
4 sampled video frames.
"""

from .k2a_hash import compute_video_k2a_hash


def compute_video_phash(video_path: str) -> str:
    """
    Compute K2A-Hash for video evidence integrity.

    Samples 4 frames from the video and builds a 64-bit temporal hash,
    zero-padded into a 256-bit field, where the diagonal traversal crosses
    both spatial blocks and time.

    Returns:
        0x-prefixed 64-char hex string (bytes32 compatible).

    Raises:
        K2AHashError: If the video cannot be decoded. Callers must not
            substitute another digest.
    """
    return compute_video_k2a_hash(video_path)
