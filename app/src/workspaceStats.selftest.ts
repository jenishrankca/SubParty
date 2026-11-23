/** Run: npx --yes tsx src/workspaceStats.selftest.ts */
import { buildReviewQueue, computeQualitySummary, countOverlaps, processingStages } from "./workspaceStats";
import type { Cue } from "./workspaceTypes";

function assert(cond: unknown, msg: string): void {
  if (!cond) throw new Error(msg);
}

const cues: Cue[] = [
  { index: 1, start: 0, end: 2, text_zh: "a", text_en: "A", uncertain: true },
  { index: 2, start: 1.5, end: 3, text_zh: "b", text_en: "", audio_disagreement: true },
  { index: 3, start: 4, end: 5, text_zh: "c", text_en: "C", maturity: "final" },
  { index: 4, start: 6, end: 7, text_zh: "d", text_en: "D", inferred: true },
];

assert(countOverlaps(cues) === 1, "overlap");
const q = computeQualitySummary(cues);
assert(q.missingEn === 1 && q.audioDisagreements === 1 && q.uncertain === 1, "quality");
const stages = processingStages("running", { note: "audio-verify", audioVerify: true, progress: 0.99 });
assert(stages.find((s) => s.id === "audio")?.active === true, "audio stage");
const rq = buildReviewQueue(cues);
assert(rq.length === 3, "review queue size");
assert(rq[0].reasons.includes("audio"), "audio first");
assert(rq.some((i) => i.reasons.includes("inferred")), "inferred in queue");
console.log("workspaceStats.selftest OK");
