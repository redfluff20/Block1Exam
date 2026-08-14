import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.db import get_conn
from app.llm.client import chat_json
from app.ingest.images import IMAGES_ROOT

CAPTION_BATCH_SIZE = 8

# Formats the vision API can ingest; skip others (e.g. .wmf/.tiff) per-slide.
_VISION_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}

CAPTION_SYSTEM = (
    "You are a medical educator writing concise image captions for lecture slides. "
    "Given each slide's page image, its extracted text, and (when present) an on-device "
    "OCR reading of the image, produce a SHORT caption (1-3 sentences, under 60 words) "
    "telling a medical student what the slide shows and the high-yield takeaway. "
    "Use the slide text, OCR text, and the image together. "
    "OCR text is authoritative for what is printed in the image: if the image contains a "
    "practice/quiz question (e.g. a PollEverywhere activity, a past-exam style question, "
    "or a textbook review question), caption it as a practice question and state what it "
    "asks. Only write 'No meaningful image content.' if the image AND all provided text "
    "are empty or pure boilerplate (page numbers, logos, 'Thank you'). "
    "Never invent facts not supported by what you see or read."
)

CAPTION_BATCH_PROMPT = """Write a short caption for each slide in this batch.

Each slide is presented as:
SLIDE <index> | Slide number: <slide_num>
<slide text>
<OCR text of the image, if any>
(An image for this slide is attached.)

Rules:
- Use the slide TEXT and OCR TEXT as the source of truth for what the slide is about.
  The image may look like a blank poll/activity screen - that is fine, the text tells
  you what it is.
- If the slide or OCR text is a question (e.g. an in-lecture activity, PollEverywhere,
  or a past-exam style practice question), the caption should state what the question asks.
- Only use 'No meaningful image content.' when the slide text AND OCR text are
  empty/boilerplate AND the image shows nothing of substance.
- Exactly one caption per slide.

Return ONLY JSON:
{{
  "captions": [
    {{"slide_index": 0, "caption": "..."}},
    {{"slide_index": 1, "caption": "..."}}
  ]
}}

- slide_index must be an index from 0 to {count_minus_1} matching the slide's position in this batch.
"""


def _slides_for_captions(conn, lecture_id):
    return conn.execute(
        "SELECT s.id, s.slide_num, s.text, s.caption FROM slides s "
        "WHERE s.lecture_id=? "
        "AND EXISTS (SELECT 1 FROM slide_images si WHERE si.slide_id=s.id) "
        "ORDER BY s.slide_num",
        (lecture_id,),
    ).fetchall()


def _slide_image_paths(conn, slide_id):
    rows = conn.execute(
        "SELECT path FROM slide_images WHERE slide_id=? ORDER BY seq",
        (slide_id,),
    ).fetchall()
    return [
        os.path.join(IMAGES_ROOT, r["path"]) for r in rows
        if os.path.splitext(r["path"])[1].lower() in _VISION_IMAGE_EXTS
    ]


def _ocr_image(path):
    """OCR an image using Apple's on-device Vision framework (free)."""
    try:
        import Vision
        from Foundation import NSURL
        from Quartz import CIImage
    except ImportError:
        return ""
    try:
        url = NSURL.fileURLWithPath_(path)
        ci = CIImage.imageWithContentsOfURL_(url)
        if not ci:
            return ""
        handler = Vision.VNImageRequestHandler.alloc().initWithCIImage_options_(ci, None)
        req = Vision.VNRecognizeTextRequest.alloc().init()
        req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        handler.performRequests_error_([req], None)
        lines = []
        for r in (req.results() or []):
            cand = r.topCandidates_(1)
            if cand and len(cand):
                lines.append(cand[0].string())
        return "\n".join(lines)
    except Exception:
        return ""


def _ocr_slide(slide):
    """Best-effort OCR of the slide's images; returns combined text."""
    texts = []
    for p in (slide.get("_images") or []):
        t = _ocr_image(p)
        if t.strip():
            texts.append(t)
    return "\n".join(texts)


_BOILERPLATE = re.compile(
    r"^(thank you|thanks|questions\??|any questions\??|the end|"
    r"copyright|slide \d+|page \d+|\d+)\s*$",
    re.IGNORECASE,
)


def _clean_ocr_caption(ocr_text, max_len=180):
    """Collapse OCR lines into a short, readable caption."""
    lines = [ln.strip() for ln in (ocr_text or "").splitlines() if ln.strip()]
    if not lines:
        return ""
    if len(lines) == 1 and _BOILERPLATE.search(lines[0]):
        return ""
    text = " ".join(lines)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_len]


def fix_no_meaningful_captions():
    """Repair slides whose caption is 'No meaningful image content.' by OCR-ing the
    image with Apple's on-device Vision framework and using the recognized text as
    the caption. Genuinely blank/boilerplate slides are left untouched.
    Returns (fixed, skipped)."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT s.id, s.slide_num, s.text FROM slides s "
        "WHERE s.caption LIKE '%No meaningful image content%' "
        "AND EXISTS (SELECT 1 FROM slide_images si WHERE si.slide_id=s.id) "
        "ORDER BY s.slide_num"
    ).fetchall()
    fixed = 0
    skipped = 0
    for r in rows:
        slide = dict(r)
        slide["_images"] = _slide_image_paths(conn, slide["id"])
        ocr = _ocr_slide(slide)
        caption = _clean_ocr_caption(ocr)
        if not caption:
            skipped += 1
            continue
        conn.execute("UPDATE slides SET caption=? WHERE id=?", (caption, slide["id"]))
        fixed += 1
        print(f"  fixed slide {slide['slide_num']}: {caption[:70]}")
    conn.commit()
    conn.close()
    return fixed, skipped


def _build_batch_prompt(batch):
    blocks = []
    images = []
    for i, s in enumerate(batch):
        slide_text = (s["text"] or "").strip()
        if slide_text:
            text_block = slide_text
        else:
            # Image-only slide: OCR the image so the captioner reads embedded
            # practice questions (PollEv, past-exam style) instead of bailing.
            ocr = _ocr_slide(s)
            text_block = f"[OCR of image]: {ocr}" if ocr.strip() else "(no extractable text)"
        blocks.append(
            f"SLIDE {i} | Slide number: {s['slide_num']}\n{text_block}"
        )
        images.extend(s["_images"] or [])
    prompt = CAPTION_BATCH_PROMPT.format(
        count=len(batch),
        count_minus_1=len(batch) - 1,
    ) + "\n\nSLIDES:\n" + "\n\n".join(blocks)
    return prompt, images


def _caption_batch(batch):
    """Generate captions for a batch of slides in one vision call.
    Returns list of (slide_id, caption) tuples (only successful)."""
    if not batch:
        return []
    prompt, images = _build_batch_prompt(batch)
    result = chat_json(
        prompt,
        system=CAPTION_SYSTEM,
        temperature=0.3,
        max_tokens=min(4000, 600 + 250 * len(batch)),
        images=images or None,
    )
    out = []
    by_index = {i: s for i, s in enumerate(batch)}
    for c in result.get("captions") or []:
        try:
            idx = int(c.get("slide_index", -1))
        except (ValueError, TypeError):
            continue
        if idx not in by_index:
            continue
        caption = (c.get("caption") or "").strip()
        if caption:
            out.append((by_index[idx]["id"], caption))
    return out


def generate_captions_for_lecture(lecture_id, force=False, on_progress=None):
    """Generate a vision caption for each slide that has a page image (batched).
    Returns number of captions generated."""
    conn = get_conn()
    slides = _slides_for_captions(conn, lecture_id)
    if force:
        slides = [dict(s) for s in slides]
    else:
        slides = [
            dict(s) for s in slides
            if not (s["caption"] and s["caption"].strip())
        ]
    for s in slides:
        s["_images"] = _slide_image_paths(conn, s["id"])
    conn.close()

    batches = [slides[i:i + CAPTION_BATCH_SIZE] for i in range(0, len(slides), CAPTION_BATCH_SIZE)]
    results = []
    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = [ex.submit(_caption_batch, b) for b in batches]
        for fut in as_completed(futures):
            try:
                results.extend(fut.result())
            except Exception as e:
                print(f"caption batch failed: {e}")

    conn = get_conn()
    generated = 0
    for slide_id, caption in results:
        conn.execute("UPDATE slides SET caption=? WHERE id=?", (caption, slide_id))
        generated += 1
    conn.commit()
    conn.close()
    if on_progress and slides:
        on_progress(generated, len(slides), generated)
    return generated
