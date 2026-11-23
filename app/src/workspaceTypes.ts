/** Shared workspace types (UI). Mirrors engine cue payload fields — no invented metrics. */

export type JobStatusLike =
  | "idle"
  | "queued"
  | "running"
  | "paused"
  | "done"
  | "error"
  | "cancelled";

export interface CueRevisionView {
  revision_id?: string;
  text_en?: string;
  text_zh?: string;
  previous_en?: string | null;
  reason?: string;
  maturity?: string;
  provisional?: boolean;
  approved?: boolean;
}

export interface Cue {
  index: number;
  start: number;
  end: number;
  text_zh: string;
  text_en: string;
  confidence?: number;
  ocr_confidence?: number;
  timing_confidence?: number;
  uncertain?: boolean;
  provisional?: boolean;
  approved?: boolean;
  maturity?: "provisional" | "context-refined" | "context-complete" | "final";
  cue_id?: string;
  revision_count?: number;
  original_ocr?: string;
  audio_transcript?: string;
  reconciled_zh?: string;
  audio_disagreement?: boolean;
  audio_evidence?: {
    asr_confidence?: number;
    char_similarity?: number;
    notes?: string;
    disagreement?: boolean;
  };
  revisions?: CueRevisionView[];
}
