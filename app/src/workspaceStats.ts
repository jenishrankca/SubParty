/**
 * UI-only derived stats from real cue / job fields.
 * Does not invent confidence or progress — only aggregates what the engine already emitted.
 */

import type { Cue, JobStatusLike } from "./workspaceTypes";

export type { Cue };

export interface QualitySummary {
  cueCount: number;
  uncertain: number;
  missingEn: number;
  audioDisagreements: number;
  provisional: number;
  overlaps: number;
  maturity: Record<string, number>;
}

export interface ProcessingStage {
  id: string;
  label: string;
  active: boolean;
  done: boolean;
}

/** Count interval overlaps from real cue start/end (not algorithmic timing changes). */
export function countOverlaps(cues: Cue[]): number {
  const sorted = [...cues].sort((a, b) => a.start - b.start);
  let n = 0;
  for (let i = 0; i < sorted.length - 1; i++) {
    if (sorted[i].end > sorted[i + 1].start + 0.05) n += 1;
  }
  return n;
}

export function computeQualitySummary(cues: Cue[]): QualitySummary {
  const maturity: Record<string, number> = {};
  let uncertain = 0;
  let missingEn = 0;
  let audioDisagreements = 0;
  let provisional = 0;
  for (const c of cues) {
    if (c.uncertain || (typeof c.ocr_confidence === "number" && c.ocr_confidence < 0.78)) {
      uncertain += 1;
    }
    if (!(c.text_en || "").trim() && (c.text_zh || "").trim()) missingEn += 1;
    if (c.audio_disagreement) audioDisagreements += 1;
    if (c.provisional || c.maturity === "provisional") provisional += 1;
    const m = c.maturity || (c.provisional ? "provisional" : "unknown");
    maturity[m] = (maturity[m] || 0) + 1;
  }
  return {
    cueCount: cues.length,
    uncertain,
    missingEn,
    audioDisagreements,
    provisional,
    overlaps: countOverlaps(cues),
    maturity,
  };
}

/**
 * Map real job status + optional SSE note to named stages.
 * Progress percent still comes from job.progress — never fabricated here.
 */
export function processingStages(
  status: JobStatusLike,
  opts: { note?: string | null; audioVerify?: boolean; audioDone?: boolean; progress?: number }
): ProcessingStage[] {
  const note = (opts.note || "").toLowerCase();
  const running = status === "running" || status === "paused" || status === "queued";
  const done = status === "done";
  const audioPhase = note.includes("audio");
  const stages: ProcessingStage[] = [
    {
      id: "ingest",
      label: "Ingest",
      active: status === "queued",
      done: running || done || status === "error" || status === "cancelled",
    },
    {
      id: "ocr",
      label: "OCR + timing",
      active: status === "running" && !audioPhase,
      done: done || audioPhase || (typeof opts.progress === "number" && opts.progress >= 0.98),
    },
    {
      id: "context",
      label: "Context refine",
      active: status === "running" && !audioPhase && (opts.progress ?? 0) > 0.05,
      done: done || audioPhase,
    },
    {
      id: "audio",
      label: "Audio verify",
      active: Boolean(opts.audioVerify && (audioPhase || (done && !opts.audioDone))),
      done: Boolean(opts.audioDone) || (done && !opts.audioVerify),
    },
    {
      id: "final",
      label: "Final",
      active: false,
      done,
    },
  ];
  if (status === "error" || status === "cancelled") {
    return stages.map((s) => ({ ...s, active: false }));
  }
  return stages;
}

export function latestRevisionPair(cue: Cue): { before?: string; after?: string; reason?: string } | null {
  const revs = cue.revisions;
  if (!revs || !revs.length) {
    if ((cue.revision_count || 0) > 1) {
      return { after: cue.text_en, reason: "revised" };
    }
    return null;
  }
  const last = revs[revs.length - 1];
  return {
    before: last.previous_en || undefined,
    after: last.text_en || cue.text_en,
    reason: last.reason,
  };
}
