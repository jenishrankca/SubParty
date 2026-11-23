import type { Cue } from "./workspaceTypes";
import { latestRevisionPair } from "./workspaceStats";

export interface CueInspectorProps {
  cue: Cue | null;
  bilingual: boolean;
  onChange: (cue: Cue) => void;
  onSave: () => void;
  onCancel: () => void;
  onSplit: () => void;
  onMarkMerge: () => void;
  onJump: () => void;
  busy?: boolean;
}

function pct(n: number | undefined): string {
  if (typeof n !== "number" || Number.isNaN(n)) return "—";
  return `${Math.round(n * 100)}%`;
}

export function CueInspector({
  cue,
  bilingual,
  onChange,
  onSave,
  onCancel,
  onSplit,
  onMarkMerge,
  onJump,
  busy,
}: CueInspectorProps) {
  if (!cue) {
    return (
      <div className="inspector empty-inspector">
        <h2>Inspector</h2>
        <p>Select a cue on the timeline to inspect evidence, translation, and revisions.</p>
      </div>
    );
  }

  const rev = latestRevisionPair(cue);
  const ocr = cue.original_ocr || cue.text_zh;
  const asr = cue.audio_transcript || "";

  return (
    <div className="inspector">
      <div className="inspector-head">
        <h2>Inspector</h2>
        <span className={`maturity-pill maturity-${cue.maturity || (cue.provisional ? "provisional" : "unknown")}`}>
          {cue.maturity || (cue.provisional ? "provisional" : "—")}
        </span>
      </div>

      <div className="inspector-meta">
        <div>
          <span>OCR conf</span>
          <strong>{pct(cue.ocr_confidence ?? cue.confidence)}</strong>
        </div>
        <div>
          <span>Timing conf</span>
          <strong>{pct(cue.timing_confidence)}</strong>
        </div>
        <div>
          <span>ASR conf</span>
          <strong>{pct(cue.audio_evidence?.asr_confidence)}</strong>
        </div>
        <div>
          <span>Revisions</span>
          <strong>{cue.revision_count ?? cue.revisions?.length ?? 1}</strong>
        </div>
      </div>

      <section>
        <h3>Evidence</h3>
        <div className="evidence-grid">
          <div>
            <span>OCR</span>
            <p>{ocr || "—"}</p>
          </div>
          <div className={cue.audio_disagreement ? "disagree" : undefined}>
            <span>ASR</span>
            <p>{asr || "—"}</p>
          </div>
          {cue.reconciled_zh ? (
            <div>
              <span>Reconciled ZH</span>
              <p>{cue.reconciled_zh}</p>
            </div>
          ) : null}
          {cue.audio_evidence?.notes ? (
            <div>
              <span>Compare</span>
              <p>
                {cue.audio_evidence.notes}
                {typeof cue.audio_evidence.char_similarity === "number"
                  ? ` · sim ${Math.round(cue.audio_evidence.char_similarity * 100)}%`
                  : ""}
              </p>
            </div>
          ) : null}
        </div>
      </section>

      <section>
        <h3>Translation</h3>
        <label>
          ZH
          <textarea
            rows={2}
            value={cue.text_zh}
            onChange={(e) => onChange({ ...cue, text_zh: e.target.value })}
          />
        </label>
        <label>
          EN
          <textarea
            rows={2}
            value={cue.text_en}
            onChange={(e) => onChange({ ...cue, text_en: e.target.value })}
          />
        </label>
        <div className="form-row tight">
          <label>
            Start
            <input
              type="number"
              step="0.05"
              value={cue.start}
              onChange={(e) => onChange({ ...cue, start: Number(e.target.value) })}
            />
          </label>
          <label>
            End
            <input
              type="number"
              step="0.05"
              value={cue.end}
              onChange={(e) => onChange({ ...cue, end: Number(e.target.value) })}
            />
          </label>
        </div>
      </section>

      {rev ? (
        <section>
          <h3>Revision</h3>
          {rev.before ? (
            <div className="rev-pair">
              <span>Before</span>
              <p>{rev.before}</p>
            </div>
          ) : null}
          <div className="rev-pair">
            <span>After{rev.reason ? ` · ${rev.reason}` : ""}</span>
            <p>{rev.after || cue.text_en}</p>
          </div>
          {cue.revisions && cue.revisions.length > 1 ? (
            <ul className="rev-list">
              {cue.revisions.slice(-6).map((r, i) => (
                <li key={r.revision_id || i}>
                  {r.reason || "revise"}
                  {r.previous_en ? `: ${r.previous_en} → ` : ": "}
                  {r.text_en}
                </li>
              ))}
            </ul>
          ) : null}
        </section>
      ) : null}

      {!bilingual ? null : null}

      <div className="form-row">
        <button type="button" onClick={onSave} disabled={busy}>
          Save
        </button>
        <button type="button" className="secondary" onClick={onJump}>
          Jump
        </button>
        <button type="button" className="secondary" onClick={onSplit} disabled={busy}>
          Split
        </button>
        <button type="button" className="secondary" onClick={onMarkMerge} disabled={busy}>
          Mark merge
        </button>
        <button type="button" className="secondary" onClick={onCancel}>
          Close
        </button>
      </div>
    </div>
  );
}
