"""Speech alignment experiments — separate from visual OCR timing.

ForcedAligner (Qwen3-ForcedAligner-0.6B, Apache-2.0) and TinyAligner are
**not** on the Live OCR path. Visual hardsub bounds ≠ speech bounds; use these
only in Quality / offline reconciliation experiments.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("subparty.align_experiments")


@dataclass
class AlignExperimentResult:
    experiment_id: str
    ok: bool
    skipped: bool = False
    reason: str | None = None
    infer_s: float = 0.0
    words: list[dict[str, Any]] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "ok": self.ok,
            "skipped": self.skipped,
            "reason": self.reason,
            "infer_s": round(self.infer_s, 3),
            "word_count": len(self.words),
            "preview": self.words[:8],
            "meta": dict(self.meta or {}),
        }


def forced_aligner_status() -> dict[str, Any]:
    has = False
    try:
        import qwen_asr  # noqa: F401

        has = True
    except Exception:
        pass
    return {
        "id": "qwen3-forced-aligner-0.6b",
        "license": "Apache-2.0",
        "available": has and bool(os.environ.get("SUBPARTY_FORCED_ALIGNER")),
        "package_hint": has,
        "install": "pip install -U qwen-asr; set SUBPARTY_FORCED_ALIGNER=1",
        "model": "Qwen/Qwen3-ForcedAligner-0.6B",
        "note": "Separate experiment — timestamps a provided transcript vs audio; not Live OCR timing",
    }


def tiny_aligner_status() -> dict[str, Any]:
    return {
        "id": "tinyaligner",
        "license": "TBD per upstream",
        "available": False,
        "install": "experiment stub — wire when a clear Apache/MIT TinyAligner package is chosen",
        "note": "Placeholder for lightweight forced alignment experiments",
    }


def run_forced_aligner(
    wav_path: Path | str,
    transcript: str,
) -> AlignExperimentResult:
    """Align ``transcript`` to ``wav_path`` via Qwen3-ForcedAligner when enabled."""
    exp_id = "qwen3-forced-aligner-0.6b"
    if not (os.environ.get("SUBPARTY_FORCED_ALIGNER") or "").strip():
        return AlignExperimentResult(
            experiment_id=exp_id,
            ok=False,
            skipped=True,
            reason="set SUBPARTY_FORCED_ALIGNER=1 to run (separate experiment)",
            meta=forced_aligner_status(),
        )
    t0 = time.perf_counter()
    try:
        # Package API is evolving; keep defensive and document failures
        try:
            from qwen_asr import Qwen3ForcedAligner  # type: ignore
        except Exception as exc:
            return AlignExperimentResult(
                experiment_id=exp_id,
                ok=False,
                skipped=True,
                reason=f"qwen_asr ForcedAligner import failed: {exc}",
                infer_s=time.perf_counter() - t0,
            )
        aligner = Qwen3ForcedAligner.from_pretrained("Qwen/Qwen3-ForcedAligner-0.6B")  # type: ignore[attr-defined]
        out = aligner.align(str(wav_path), transcript)  # type: ignore[attr-defined]
        words: list[dict[str, Any]] = []
        if isinstance(out, dict):
            for w in out.get("words") or out.get("timestamps") or []:
                if isinstance(w, dict):
                    words.append(w)
                else:
                    words.append({"token": str(w)})
        elif isinstance(out, list):
            words = [x if isinstance(x, dict) else {"token": str(x)} for x in out]
        return AlignExperimentResult(
            experiment_id=exp_id,
            ok=True,
            infer_s=time.perf_counter() - t0,
            words=words,
            meta={"model": "Qwen/Qwen3-ForcedAligner-0.6B", "license": "Apache-2.0"},
        )
    except Exception as exc:
        return AlignExperimentResult(
            experiment_id=exp_id,
            ok=False,
            infer_s=time.perf_counter() - t0,
            reason=f"{type(exc).__name__}: {exc}",
        )


def run_tiny_aligner(
    wav_path: Path | str,
    transcript: str,
) -> AlignExperimentResult:
    return AlignExperimentResult(
        experiment_id="tinyaligner",
        ok=False,
        skipped=True,
        reason="TinyAligner not wired — experiment stub only",
        meta=tiny_aligner_status(),
    )


def list_align_experiments() -> list[dict[str, Any]]:
    return [forced_aligner_status(), tiny_aligner_status()]
