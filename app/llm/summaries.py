import json

from app.db import get_conn
from app.llm.client import chat_json_text

# Chunk size (slides per summary call). Text-only via DeepSeek is cheap + fast.
SUMMARY_CHUNK = 30

SUMMARY_SYSTEM = (
    "You are a senior medical school lecturer writing a condensed study summary "
    "for a first-year medical student about to take a block exam. "
    "You are precise, organized, and prioritize memorizable high-yield facts. "
    "You NEVER invent facts not present in the provided lecture text. "
    "Format tables for anything tabular (transfer categories, timing, lists of structures)."
)

SUMMARY_PROMPT = """Here are slides {start}-{end} from a lecture titled "{title}".

Each slide is shown as its slide number, its extracted text, and a vision-derived
caption of its image content (the caption describes what the slide's figure shows).

SLIDES:
{slides}

Write a condensed study summary of THESE slides with EXACTLY this JSON structure:
{{
  "summary": "Markdown. Condensed narrative grouped into sections with headers (##). Cover every distinct concept in these slides - do not drop facts. Use tables where the source material is tabular.",
  "key_points": ["list of short, high-yield, memorizable bullets - the facts most likely to appear on a single-best-answer MCQ exam"]
}}

Rules:
- If a slide is a title/header slide or has no meaningful content, skip it.
- Preserve exact numbers, dates, names, and classifications from the source.
- For anything clearly missing or empty, do not fabricate.
- CITATIONS: At the end of each paragraph (or each bullet) that drew facts from specific slides, add a citation in this exact format: (slides N, M) or (slide N) - using the slide numbers shown in the "--- SLIDE N ---" markers. Only cite slides you actually used. If a paragraph synthesizes many slides, list the range, e.g. (slides 4-9).
"""

POLISH_SYSTEM = (
    "You are a senior medical school lecturer consolidating a draft study summary "
    "into a single polished, exam-ready document. You NEVER invent facts not present "
    "in the draft. You MUST preserve every slide citation exactly."
)

POLISH_PROMPT = """The following is a draft study summary for a lecture titled "{title}",
assembled from sections. It may have overlapping or repeated section headers and
uneven flow because it was written in chunks.

DRAFT:
{draft}

Rewrite it into ONE coherent, polished study summary:
- Merge duplicate sections/headers; keep every distinct concept and fact.
- CRITICAL: Every fact in the draft cites its source slides in parentheses, e.g.
  "(slide 4)" or "(slides 8-12)". You MUST carry each citation over to the polished
  output in the same place, right after the fact it supports. Never drop a citation.
- If a fact came from a specific slide, keep citing it. Do not add new citations for
  facts the draft did not cite, and do not remove the ones it did.
- Preserve exact numbers, names, and classifications.
- Do not add new facts. Keep tables where the source was tabular.

Return ONLY JSON with EXACTLY this structure:
{{
  "summary": "Markdown. The full polished summary with clear ## section headers and every citation preserved.",
  "key_points": ["list of the strongest 8-15 high-yield, memorizable bullets for a single-best-answer MCQ exam. Keep slide citations on the bullets that have them."]
}}
"""


def _build_chunk_prompt(title, chunk):
    """Build a text-only prompt for one chunk of slides (captions carry image info)."""
    blocks = []
    for r in chunk:
        slide_text = (r["text"] or "").strip()
        caption = (r["caption"] or "").strip()
        note = ""
        if caption:
            note = f"\n[Image caption]: {caption}"
        blocks.append(f"--- SLIDE {r['slide_num']} ---\n{slide_text}{note}")
    return SUMMARY_PROMPT.format(
        title=title,
        start=chunk[0]["slide_num"],
        end=chunk[-1]["slide_num"],
        slides="\n\n".join(blocks),
    )


def _load_chunks(conn, lecture_id):
    rows = conn.execute(
        "SELECT id, slide_num, text, caption FROM slides "
        "WHERE lecture_id=? ORDER BY slide_num",
        (lecture_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def generate_summary(lecture_id, on_progress=None):
    conn = get_conn()
    row = conn.execute(
        "SELECT title FROM lectures WHERE id=?", (lecture_id,)
    ).fetchone()
    if not row:
        conn.close()
        raise ValueError(f"No lecture {lecture_id}")
    title = row["title"]

    conn.execute(
        "UPDATE lectures SET summary_status='generating' WHERE id=?", (lecture_id,)
    )
    conn.commit()

    chunks = _load_chunks(conn, lecture_id)
    conn.close()

    chunk_list = [chunks[i:i + SUMMARY_CHUNK] for i in range(0, len(chunks), SUMMARY_CHUNK)] or [[]]

    summaries = []
    key_points = []
    try:
        for chunk in chunk_list:
            if not chunk:
                continue
            prompt = _build_chunk_prompt(title, chunk)
            result = chat_json_text(
                prompt,
                system=SUMMARY_SYSTEM,
                temperature=0.3,
                max_tokens=4000,
            )
            summaries.append(result.get("summary") or "")
            key_points.extend(result.get("key_points") or [])
    except Exception as e:
        conn = get_conn()
        conn.execute(
            "UPDATE lectures SET summary_status='error' WHERE id=?", (lecture_id,)
        )
        conn.commit()
        conn.close()
        raise e

    body = "\n\n".join(s for s in summaries if s)

    # Polish pass: merge the chunk sections into one coherent document (DeepSeek, text).
    if body.strip():
        try:
            polished = chat_json_text(
                POLISH_PROMPT.format(title=title, draft=body),
                system=POLISH_SYSTEM,
                temperature=0.2,
                max_tokens=6000,
            )
            pbody = (polished.get("summary") or "").strip()
            pkey = polished.get("key_points")
            if pbody:
                body = pbody
            if pkey:
                key_points = pkey
        except Exception as e:
            print(f"summary polish failed for lecture {lecture_id}: {e}")
    conn = get_conn()
    conn.execute(
        "INSERT INTO summaries(lecture_id, body, key_points) VALUES(?,?,?) "
        "ON CONFLICT(lecture_id) DO UPDATE SET body=excluded.body, "
        "key_points=excluded.key_points, created_at=datetime('now')",
        (lecture_id, body, json.dumps(key_points)),
    )
    conn.execute(
        "UPDATE lectures SET summary_status='done' WHERE id=?", (lecture_id,)
    )
    conn.commit()
    conn.close()
    return {"summary": body, "key_points": key_points}


def get_summary(lecture_id):
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM summaries WHERE lecture_id=?", (lecture_id,)
    ).fetchone()
    conn.close()
    return row
