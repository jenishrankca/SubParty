"""SubParty 2.1 — maturity, frame timing, retroactive refine, metrics, engine compare."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from context_translate import DialogueContext
from dialogue_buffer import DialogueBuffer
from metrics import ocr_hit_rate, realtime_factor, summarize_job_metrics, timing_errors
from refine import context_fingerprint, finalize_cues_at_eof, refine_cue_with_buffer, should_attempt_refine
from revision import CueRecord
from timing_engine import refine_boundaries


class FakeDialCtx(DialogueContext):
    """Deterministic translator: bare vs contextual differ when upcoming names 李逍遥."""

    def translate(
        self,
        zh: str,
        upcoming: list[str] | None = None,
        prior_override: list[str] | None = None,
        compare_candidates: bool = True,
    ) -> str:
        zh = (zh or "").strip()
        upcoming = upcoming or []
        if zh == "他来了":
            if any("李逍遥" in u for u in upcoming):
                return "Li Xiaoyao has arrived"
            return "He came"
        if zh == "李逍遥进门了":
            return "Li Xiaoyao entered"
        if zh == "我们跟他走吧":
            return "Let's go with him"
        if zh == "走吧":
            return "Let's go"
        if zh == "小心":
            return "Careful"
        return zh


class TestMaturityAndRetroactive(unittest.TestCase):
    def test_high_confidence_not_permanently_skipped(self) -> None:
        rec = CueRecord.new(
            text_zh="他来了",
            start=1.0,
            end=2.0,
            text_en="He came",
            ocr_confidence=0.95,
            timing_confidence=0.9,
            reason="provisional",
            future_horizon_s=120.0,
        )
        # Simulate early promote (old 2.0 path would then skip forever)
        rec.revise(
            provisional=False,
            approved=True,
            maturity="context-refined",
            reason="promoted-with-context-unchanged",
            context_fingerprint=context_fingerprint([], []),
        )
        self.assertTrue(
            should_attempt_refine(rec, fingerprint=context_fingerprint([], ["李逍遥进门了"]))
        )

        buf = DialogueBuffer(window_s=120.0)
        buf.add(1.0, "他来了", en="He came", cue_id=rec.cue_id)
        buf.add(5.0, "李逍遥进门了", en="Li Xiaoyao entered")
        buf.add(8.0, "我们跟他走吧", en="Let's go")
        out = refine_cue_with_buffer(
            rec,
            dial_ctx=FakeDialCtx(),
            buffer=buf,
            observed_until=10.0,
        )
        self.assertIsNotNone(out)
        self.assertIn("Li Xiaoyao", rec.text_en)
        self.assertIn(rec.maturity, {"context-refined", "context-complete"})

    def test_maturity_complete_at_horizon(self) -> None:
        rec = CueRecord.new(
            text_zh="走吧",
            start=0.0,
            end=1.0,
            text_en="Let's go",
            ocr_confidence=0.9,
            timing_confidence=0.85,
            future_horizon_s=10.0,
        )
        buf = DialogueBuffer(window_s=120.0)
        buf.add(0.0, "走吧", en="Let's go", cue_id=rec.cue_id)
        refine_cue_with_buffer(
            rec, dial_ctx=FakeDialCtx(), buffer=buf, observed_until=10.0, eof=False
        )
        # Horizon elapsed (start 0 + 10s) → context-complete even without EN change
        self.assertEqual(rec.maturity, "context-complete")
        self.assertFalse(rec.provisional)

    def test_finalize_marks_final(self) -> None:
        rec = CueRecord.new(
            text_zh="小心",
            start=0.0,
            end=1.0,
            text_en="Careful",
            ocr_confidence=0.9,
            timing_confidence=0.85,
        )
        buf = DialogueBuffer(window_s=120.0)
        buf.add(0.0, "小心", cue_id=rec.cue_id)
        finalize_cues_at_eof([rec], dial_ctx=FakeDialCtx(), buffer=buf, observed_until=30.0)
        self.assertEqual(rec.maturity, "final")
        self.assertFalse(rec.provisional)


class TestFrameTiming(unittest.TestCase):
    def test_fine_edge_with_probe(self) -> None:
        # Coarse grid: text present at ticks 10–12 (5.0–6.0s with tick=0.5)
        slot_text = {9: "", 10: "走吧", 11: "走吧", 12: "走吧", 13: ""}
        slot_conf = {10: 0.95, 11: 0.9, 12: 0.92}

        def probe(t: float) -> tuple[str, float]:
            # True onset at 4.7s, offset at 6.3s — sub-0.5s vs coarse 5.0 / 6.5
            if 4.7 <= t <= 6.3:
                return "走吧", 0.9
            return "", 0.0

        result = refine_boundaries(
            zh="走吧",
            rough_start=5.0,
            rough_end=6.5,
            slot_text=slot_text,
            slot_conf=slot_conf,
            base_tick=0.5,
            duration=20.0,
            high_accuracy=True,
            fine_step_s=0.1,
            probe_zh=probe,
        )
        self.assertEqual(result.evidence.method, "frame-level")
        self.assertAlmostEqual(result.start, 4.7, places=1)
        self.assertLess(abs(result.end - 6.4), 0.15)
        self.assertLess(abs(result.start - 4.7), 0.15)


class TestMetrics(unittest.TestCase):
    def test_independent_metrics_and_tight_timing_tol(self) -> None:
        gold = [
            {"start": 0.0, "end": 1.4, "text_zh": "修仙不如吃饭", "text_en": "Cultivation isn't as good as eating"},
            {"start": 1.5, "end": 2.9, "text_zh": "李逍遥来了"},
        ]
        pred = [
            {"start": 0.1, "end": 1.5, "text_zh": "修仙不如吃饭", "text_en": "Cultivation isn't as good as eating"},
            {"start": 1.6, "end": 3.0, "text_zh": "李逍遥来了", "text_en": "Li Xiaoyao came"},
        ]
        ocr = ocr_hit_rate(pred, gold)
        self.assertEqual(ocr["hit"], 2)
        timing = timing_errors(pred, gold, match_tol_s=0.5)
        self.assertEqual(timing["matched"], 2)
        self.assertIsNotNone(timing["mean_start_err"])
        self.assertLess(float(timing["mean_start_err"]), 0.5)
        summary = summarize_job_metrics(
            pred_cues=pred,
            gold_cues=gold,
            processing_s=2.0,
            video_duration_s=4.0,
            live_latency_s=0.4,
            timing_tol_s=0.5,
        )
        self.assertAlmostEqual(summary["rtf"], 0.5)
        self.assertEqual(realtime_factor(10, 5), 2.0)


class TestEngineCompare(unittest.TestCase):
    def test_argos_nllb_qwen_same_samples(self) -> None:
        from engines import get_engine, list_engines

        samples = ["你好", "我们走吧", "李逍遥来了"]
        engines = {e.id: e for e in list_engines()}
        self.assertIn("argos", engines)
        results: dict[str, dict[str, str]] = {}
        for eid in ("argos", "nllb", "qwen"):
            eng = get_engine(eid)
            if not eng.available():
                results[eid] = {"_skipped": "unavailable"}
                continue
            out: dict[str, str] = {}
            for zh in samples:
                try:
                    out[zh] = eng.translate(zh)
                except Exception as exc:
                    out[zh] = f"ERROR:{exc}"
            results[eid] = out
            # Unload heavy models between engines
            unload = getattr(eng, "unload", None)
            if callable(unload):
                unload()

        # Argos must work
        self.assertNotIn("_skipped", results["argos"])
        self.assertTrue(any(results["argos"].values()))
        report_path = Path(__file__).parent / "fixtures" / "engine_compare_last.json"
        report_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print("ENGINE_COMPARE", {k: ("ok" if "_skipped" not in v else "skip") for k, v in results.items()})


class TestAmbiguousFixture(unittest.TestCase):
    def test_fixture_retroactive_story(self) -> None:
        fixture = Path(__file__).parent / "fixtures" / "annotated_ambiguous.json"
        data = json.loads(fixture.read_text(encoding="utf-8"))
        first = data["cues"][0]
        rec = CueRecord.new(
            text_zh=first["text_zh"],
            start=float(first["start"]),
            end=float(first["end"]),
            text_en=first["text_en_provisional"],
            ocr_confidence=0.92,
            timing_confidence=0.88,
            future_horizon_s=float(data["context_window_s"]),
        )
        buf = DialogueBuffer(window_s=float(data["context_window_s"]))
        for c in data["cues"]:
            buf.add(float(c["start"]), c["text_zh"], en=str(c.get("text_en_gold") or ""))
        # Promote early (high confidence) then add future context via buffer already filled
        rec.revise(
            provisional=False,
            approved=True,
            maturity="context-refined",
            reason="early-promote",
            context_fingerprint="old",
        )
        refine_cue_with_buffer(
            rec,
            dial_ctx=FakeDialCtx(),
            buffer=buf,
            observed_until=12.0,
        )
        self.assertEqual(rec.text_en, first["text_en_gold"])


if __name__ == "__main__":
    unittest.main()
