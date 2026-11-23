# SubParty MVP — Gap analysis (stages 1–9 vs `main`)

**Date:** 2026-11-23 (updated)  
**Baseline tip:** `main` (push after High polish)  
**Spec:** [subparty-mvp-spec.md](./subparty-mvp-spec.md) · **Roadmap:** [subparty-roadmap.md](./subparty-roadmap.md)

| Stage | Priority | Verdict |
| --- | --- | --- |
| 1–5 Critical | Critical | **Landed** |
| 6 Professional editor | High | **Landed** (split/merge + evidence editor) |
| 7 Independent QA | High | **Landed** (gold JSON + HTML/JSON export) |
| 8 Glossary / series / characters | High | **Landed** (character sheets) |
| 9 Batch production | High | **Landed** (durable queue, retry, export-all) |

---

## Critical — closed

CER / ASR CER / timing ms / revision / speech-align / word timestamps / ASR RTF / annotated suite — see earlier commits.

```bat
cd engine
.venv\Scripts\python.exe -m unittest tests.test_annotated_suite tests.test_mvp_metrics tests.test_speech_align -v
```

---

## High stages — closed (this pass)

| Item | Status | Notes |
| --- | --- | --- |
| Split / merge cues | Done | `cue_ops.py` · `POST .../cues/split|merge` · editor UI |
| QA gold upload | Done | `gold_path` / `gold_cues` on `/jobs/{id}/qa` · Electron `pickJson` |
| QA exportable report | Done | HTML + JSON under `data/reports/` (`export_qa_html`) |
| Character sheets | Done | `characters.py` · `/characters` CRUD · glossary sync · UI sheet |
| Durable batch queue | Done | `data/batches/{id}.json` · survives restart |
| Batch retry failed | Done | `POST /jobs/batch/{id}/retry` |
| Batch export-all | Done | `POST /jobs/batch/{id}/export` |

```bat
.venv\Scripts\python.exe -m unittest tests.test_mvp_high tests.test_mvp_high_polish -v
```

### Remaining optional polish (not blocking MVP High)
- Translation-memory archive UI
- PDF QA (HTML is the portable export; PDF optional later)
- Character aliases in translate mask
- Batch auto-resume worker on engine boot

---

## Validation targets

- OCR CER ↓ · ASR CER · timing ms · RTF · revision before→after · speech-align ms delta · multimodal disagreement rate
