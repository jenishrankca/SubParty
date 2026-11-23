"""Retroactive translation refinement when future context arrives."""

from __future__ import annotations

import logging
from typing import Any

from context_translate import DialogueContext, pick_best_candidate
from dialogue_buffer import DialogueBuffer
from revision import CueRecord
from scheduler import rank_uncertain_cues

logger = logging.getLogger("subparty.refine")


def refine_cue_with_buffer(
    record: CueRecord,
    *,
    dial_ctx: DialogueContext,
    buffer: DialogueBuffer,
    force: bool = False,
) -> CueRecord | None:
    """
    Re-translate a cue using the full rolling buffer around its start time.
    Returns the record if EN changed (revision applied), else None.
    """
    if not record.text_zh:
        return None
    if record.approved and not record.provisional and not force and record.ocr_confidence >= 0.9:
        # Stable high-confidence approved cue — skip unless forced
        if record.timing_confidence >= 0.85:
            return None

    prior, upcoming = buffer.context_zh(record.start)
    # Exclude self from upcoming/prior duplicates
    prior = [p for p in prior if p != record.text_zh][-24:]
    upcoming = [u for u in upcoming if u != record.text_zh][:24]

    new_en = dial_ctx.translate(
        record.text_zh,
        upcoming=upcoming,
        prior_override=prior,
        compare_candidates=True,
    )
    if not new_en or new_en.strip() == (record.text_en or "").strip():
        # Still mark refined if it was provisional and context is rich
        if record.provisional and (prior or upcoming):
            record.revise(
                provisional=False,
                approved=True,
                reason="promoted-with-context-unchanged",
            )
            buffer.update_en(record.cue_id, record.text_en)
            return record
        return None

    record.revise(
        text_en=new_en.strip(),
        provisional=False,
        approved=True,
        reason="retroactive-context",
    )
    buffer.update_en(record.cue_id, record.text_en)
    logger.info(
        "refined cue %s @%.2f: %r → %r",
        record.cue_id[:8],
        record.start,
        record.revisions[-1].previous_en,
        record.text_en,
    )
    return record


def refine_batch(
    records: list[CueRecord],
    *,
    dial_ctx: DialogueContext,
    buffer: DialogueBuffer,
    limit: int = 4,
) -> list[CueRecord]:
    """Quality-scheduled refine pass over provisional/uncertain cues."""
    payloads = [r.as_payload() for r in records]
    ranked = rank_uncertain_cues(payloads)
    by_id = {r.cue_id: r for r in records}
    changed: list[CueRecord] = []
    for p in ranked[:limit]:
        rid = str(p.get("cue_id") or "")
        rec = by_id.get(rid)
        if not rec:
            continue
        out = refine_cue_with_buffer(rec, dial_ctx=dial_ctx, buffer=buffer)
        if out:
            changed.append(out)
    return changed
