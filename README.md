# Block 1 Exam Prep

A UWorld-style, lecture-anchored practice engine for medical school block exams. Built from your actual lecture slides — not generic question banks, not Anki.

## How it works

```
Your lectures (PDF/PPTX)
   │  PPTX is auto-converted to PDF (PowerPoint/Keynote AppleScript)
   ▼
Per-slide pipeline (all automatic on import, in the background)
   ├─ Slide text        (PyMuPDF text extraction)
   ├─ Full-page images  (each slide rendered to an image)
   ├─ Captions          (Qwen vision model reads each slide image)
   └─ Summary           (Qwen vision model, chunked across slides, slide-cited)
   ▼
Knowledge base (SQLite)
   ├─ Lectures → Slides   (every slide is the atomic unit, coordinate = lecture, slide #)
   ├─ Summaries           (vision-condensed, high-yield, slide-cited)
   └─ Questions           (MCQ + explanation, generated on demand, anchored to a slide)
        │
        └─ Missed tracking (wrong answers tagged for next-day review)
```

Every question is anchored to a specific slide by coordinate `(lecture, slide #)`. The
question-generation model reads the slide's page image directly, so figures, diagrams,
and image content are used — there is no separate OCR or "concept" layer.

## Daily protocol

1. **Import** — upload PDF or PPTX. Captions + summary are generated automatically in the background; the Learn list shows live status until "Summary ready". No buttons to click.
2. **Learn** — open a lecture, read the condensed summary, browse the slide deck (list or grid view).
3. **Drill** — start a session (tutor or quiz mode, UWorld-style MCQs with teaching explanations).

## Session modes

- **Practice** — pick lectures (or all) and a count (max 59); fresh questions are generated on the spot, one per selected slide. In tutor mode you see the explanation immediately; in quiz mode you can change answers freely and submit for grading at the end.
- **Review missed** — replays every question you've gotten wrong. Answer it correctly and it's cleared; miss it again and it stays tagged.

Wrong answers are automatically tagged as "missed" and offered for review the next day. No rating buttons, no spaced-repetition setup — just answer, learn, and revisit what you missed.

## Quiz vs tutor

- **Tutor mode** — infinite time, explanation right after you answer, running timer. You can go back to review already-answered questions (view only).
- **Quiz mode** — total time = 1.5 min/question. You can move back and forth and change any answer until you hit **Submit quiz**; only your final selections are graded. Timeout auto-grades unanswered questions as incorrect.

## Coverage & progress

Coverage is measured in **slides**: a slide is covered once a question has been generated from it. The dashboard and Progress tab show per-lecture slide coverage, and per-slide accuracy from your answer history.

## Duplicate handling

When you import a lecture, the app fingerprints its slide text and compares against existing lectures. True duplicates (e.g. the same deck re-uploaded) are **skipped automatically** and reported in the import result.

## Question volume

Questions are generated fresh on demand (never pre-banked), capped at 59 per session. Slides with the fewest generated questions are weighted higher, so practice stays spread out while still feeling random.

## Setup

```bash
# 1. Create .env with your OpenRouter key (or add it in the app's Dashboard)
cp .env.example .env   # then add your OPENROUTER_API_KEY

# 2. Run (creates venv + installs deps on first run)
./run.sh
```

Open http://localhost:8000

Note: PPTX import requires Microsoft PowerPoint (or Keynote as fallback) installed on macOS to convert to PDF.

## First use

1. **Dashboard → Import lectures** — upload your PDF/PPTX slides. Captions + summary generate automatically.
2. **Learn** — click a lecture once the status says "Summary ready".
3. **Drill** — start a session. In tutor mode answer and read the teaching explanation; in quiz mode answer, review, then submit.

## Project layout

```
app/
  db.py            SQLite schema + migrations
  ingest/
    slides.py      PDF → slide text
    images.py      PDF page → full-page image renders
    convert.py     PPTX → PDF (PowerPoint/Keynote AppleScript)
    backfill.py    convert existing PPTX lectures to PDF in place
    duplicates.py  import-time duplicate detection
  llm/
    client.py      vision LLM client (OpenAI-compatible, image support)
    captions.py    slide image → caption
    summaries.py   chunked vision summaries
  sessiongen.py    on-demand question generation + session creation
  stats.py         coverage + accuracy stats
  main.py          FastAPI app + routes
  static/          frontend (vanilla JS, no build step)
lectures/          imported source files (PDF)
data/block1.db     local database
```

## API keys

The app is pinned to **OpenRouter** by default (`https://openrouter.ai/api/v1`, model
`qwen/qwen3-vl-8b-instruct`) — pay-per-token, no subscription.

Set your key in **one** of these places (a key saved in the app's Dashboard takes
precedence over `.env`):

1. **In the app**: Dashboard → "LLM settings" → paste your OpenRouter key (`sk-or-...`) → Save.
2. **In `.env`**: `OPENROUTER_API_KEY=sk-or-...` (auto-loaded by `run.sh`).

Legacy `LLM_*` / `DEEPSEEK_*` vars are still honored as fallbacks if set.
