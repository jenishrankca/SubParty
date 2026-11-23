# OCR + region stage (bench branch)

Branch: `cursor/subparty-engine-bench-7239`

## Live re-measure (PyAV default)

| Metric | Pre-PyAV (stage1-ish) | With PyAV |
|--------|----------------------|-----------|
| Ready | 8.67 s / 12.84 s | **6.81 s** |
| Seek@60 | 7.53 s | **2.75 s** |
| Seek2@85 | 3.57 s | **1.66 s** |

## OCR backends

| id | Role |
|----|------|
| `ppocrv6_small` | **Default** — explicit PP-OCRv6 det/rec small |
| `rapidocr` | Legacy SubParty init (implicit; also PP-OCRv6 under RapidOCR defaults) |
| `ppocrv5_mobile` | Compare / rollback |

Env: `SUBPARTY_OCR=ppocrv6_small|rapidocr|ppocrv5_mobile`

## DeepSeek-OCR-2

Optional uncertain-path **stub** only (`ocr.ocr_uncertain_deepseek`).  
Enable later with `SUBPARTY_DEEPSEEK_OCR=1`. **Never** every-frame.

## Confirmable crop

`region.ConfirmableRegion` + `confirm_region` / `clear_confirmed_region` / `get_confirmed_region`.  
SSE `region` events include `region_confirmable` for UI confirm hooks.

**UI (stage 7):** `POST /region/confirm` · `POST /region/clear` · Job panel Confirm/Clear on detected crop.

## OCR p50 (weird-games, 10 pre-extracted crops, OCR-only)

| Backend | p50 | nonempty |
|---------|-----|----------|
| Stage-1 baseline (decode+OCR combo era) | **1679 ms** | — |
| `rapidocr` legacy | **~1728 ms** | 5/10 |
| `ppocrv6_small` (default) | **1805 ms** | 5/10 |
| `ppocrv5_mobile` | **1201 ms** (faster, fewer hits) | 3/10 |

v6 stays default (better hit rate on this clip). v5 is optional compare/rollback via `SUBPARTY_OCR=ppocrv5_mobile`.

## Measure

```bat
cd engine
.venv\Scripts\python.exe scripts\compare_ocr.py
```
