"""Audio I/O helpers that avoid faster-whisper's PyAV ``metadata_errors`` path.

We already extract mono 16 kHz PCM WAV via FFmpeg; load it with the stdlib
``wave`` module and pass float32 arrays into ASR backends.
"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np


def load_wav_mono_f32(path: Path | str) -> tuple[np.ndarray, int]:
    """
    Load a PCM WAV as float32 mono in [-1, 1].

    Raises ValueError for unsupported formats (expect 16-bit PCM).
    """
    p = Path(path)
    with wave.open(str(p), "rb") as wf:
        nch = wf.getnchannels()
        sw = wf.getsampwidth()
        rate = wf.getframerate()
        nframes = wf.getnframes()
        raw = wf.readframes(nframes)
    if sw != 2:
        raise ValueError(f"expected 16-bit PCM WAV, got sampwidth={sw} for {p}")
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if nch > 1:
        audio = audio.reshape(-1, nch).mean(axis=1)
    return audio, int(rate)
