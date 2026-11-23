"""Retroactive translation refinement with SubParty 2.1 cue maturity."""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from context_translate import DialogueContext
from dialogue_buffer import DialogueBuffer
from revision import CueRecord, Maturity
from scheduler import rank_uncertain_cues

logger = logging.getLogger("subparty.refine")


def context_fingerprint(prior: list[str], upcoming: list[str]) -> str:
    raw = "||".join(prior[-24:]) + "##" + "||".join(upcoming[:24])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def maturity_for_observation(
    record: CueRecord,
    *,
    observed_until: float,
    eof: bool = False,
) -> Maturity:
    """
    Maturity clock: context-complete when observed media covers start+horizon, or EOF.
    """
    if record.maturity == "final":
        return "final"
    horizon = record.start + record.future_horizon_s
    if eof or observed_until >= horizon - 1e-3:
        return "context-complete"
    if record.maturity in {"context-refined", "context-complete"}:
        return record.maturity
    if not record.provisional and record.approved:
        return "context-refined"
    return "provisional"


def should_attempt_refine(
    record: CueRecord,
    *,
    fingerprint: str,
    force: bool = False,
) -> bool:
    """
    High-confidence approved cues stay eligible when new future dialogue arrives.
    Only context-complete / final are skipped (unless force / fingerprint change before complete).
    """
    if force:
        return True
    if not record.text_zh:
        return False
    if record.maturity == "final":
        return False
    # New future context always reopens refine until context-complete
    if fingerprint and fingerprint != record.context_fingerprint:
        if record.maturity != "context-complete":
            return True
        # Even after complete, a *new* fingerprint inside the horizon is rare;
        # allow one more pass if fingerprint changed (genuinely new dialogue).
        return True
    if record.maturity == "context-complete":
        return False
    # Never permanently skip high-OCR approved cues solely due to confidence
    return True


def refine_cue_with_buffer(
    record: CueRecord,
    *,
    dial_ctx: DialogueContext,
    buffer: DialogueBuffer,
    force: bool = False,
    observed_until: float | None = None,
    eof: bool = False,
) -> CueRecord | None:
    """
    Re-translate a cue using the rolling buffer around its start time.
    Advances maturity: provisional → context-refined → context-complete (by horizon/EOF).
    """
    if not record.text_zh:
        return None

    if observed_until is not None:
        record.observed_until = max(record.observed_until, float(observed_until))

    prior, upcoming = buffer.context_zh(record.start)
    prior = [p for p in prior if p != record.text_zh][-24:]
    upcoming = [u for u in upcoming if u != record.text_zh][:24]
    fp = context_fingerprint(prior, upcoming)

    if not should_attempt_refine(record, fingerprint=fp, force=force):
        # Still advance maturity clock when horizon elapses
        target = maturity_for_observation(
            record, observed_until=record.observed_until, eof=eof
        )
        if target != record.maturity and MATURITY_GT(target, record.maturity):
            record.revise(reason="maturity-clock", maturity=target, provisional=False, approved=True)
            return record
        return None

    new_en = dial_ctx.translate(
        record.text_zh,
        upcoming=upcoming,
        prior_override=prior,
        compare_candidates=True,
    )

    target = maturity_for_observation(
        record, observed_until=record.observed_until, eof=eof
    )
    if target == "provisional" and (prior or upcoming):
        target = "context-refined"

    changed = bool(new_en and new_en.strip() != (record.text_en or "").strip())
    if not changed:
        if record.provisional or record.maturity == "provisional" or fp != record.context_fingerprint:
            record.revise(
                provisional=False,
                approved=True,
                maturity=target if target != "provisional" else "context-refined",
                reason="promoted-with-context-unchanged",
                context_fingerprint=fp,
            )
            buffer.update_en(record.cue_id, record.text_en)
            return record
        if target != record.maturity and MATURITY_GT(target, record.maturity):
            record.revise(
                reason="maturity-clock",
                maturity=target,
                provisional=False,
                approved=True,
                context_fingerprint=fp,
            )
            return record
        return None

    record.revise(
        text_en=new_en.strip(),
        provisional=False,
        approved=True,
        maturity=target if target != "provisional" else "context-refined",
        reason="retroactive-context",
        context_fingerprint=fp,
    )
    buffer.update_en(record.cue_id, record.text_en)
    logger.info(
        "refined cue %s @%.2f maturity=%s: %r → %r",
        record.cue_id[:8],
        record.start,
        record.maturity,
        record.revisions[-1].previous_en,
        record.text_en,
    )
    return record


def MATURITY_GT(a: Maturity, b: Maturity) -> bool:
    from revision import MATURITY_ORDER

    return MATURITY_ORDER.get(a, 0) > MATURITY_ORDER.get(b, 0)


def finalize_cues_at_eof(
    records: list[CueRecord],
    *,
    dial_ctx: DialogueContext,
    buffer: DialogueBuffer,
    observed_until: float,
) -> list[CueRecord]:
    """EOF pass: refine with full buffer, mark context-complete then final."""
    changed: list[CueRecord] = []
    for rec in records:
        rec.observed_until = max(rec.observed_until, observed_until)
        out = refine_cue_with_buffer(
            rec,
            dial_ctx=dial_ctx,
            buffer=buffer,
            force=True,
            observed_until=observed_until,
            eof=True,
        )
        if out:
            changed.append(out)
        if rec.maturity != "final":
            rec.revise(
                reason="final-approve",
                maturity="final",
                provisional=False,
                approved=True,
            )
            if rec not in changed:
                changed.append(rec)
    return changed


def refine_batch(
    records: list[CueRecord],
    *,
    dial_ctx: DialogueContext,
    buffer: DialogueBuffer,
    limit: int = 4,
    observed_until: float | None = None,
    eof: bool = False,
) -> list[CueRecord]:
    """Quality-scheduled refine pass — includes high-confidence cues when context grew."""
    payloads = [r.as_payload() for r in records]
    ranked = rank_uncertain_cues(payloads)
    # Also include non-provisional cues whose fingerprint may be stale (append after)
    by_id = {r.cue_id: r for r in records}
    ordered: list[CueRecord] = []
    seen: set[str] = set()
    for p in ranked:
        rid = str(p.get("cue_id") or "")
        rec = by_id.get(rid)
        if rec and rid not in seen:
            ordered.append(rec)
            seen.add(rid)
    for rec in records:
        if rec.cue_id not in seen:
            ordered.append(rec)
            seen.add(rec.cue_id)

    changed: list[CueRecord] = []
    for rec in ordered:
        if len(changed) >= limit:
            break
        out = refine_cue_with_buffer(
            rec,
            dial_ctx=dial_ctx,
            buffer=buffer,
            observed_until=observed_until,
            eof=eof,
        )
        if out:
            changed.append(out)
    return changed
