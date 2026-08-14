"""On-the-spot practice session generation, slide-anchored.

Sessions never draw from a pre-banked question pool. When a practice session is
created we:
  1. Select slides from the chosen lectures (or all lectures).
  2. Generate a fresh question per slide via DeepSeek (capped at MAX_QUESTIONS).
  3. Record each question's coordinate (lecture_id, slide_id) and store it.
Answering tags wrong questions into the `missed` table for next-day review.

The slide is the atomic unit: every question is anchored to a specific slide's
text + OCR + caption, so there is no separate "concept" layer to drift or
duplicate content. Redundancy in questions simply mirrors redundancy in the
source slides themselves.
"""
import os
import json
import random
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.db import get_conn
from app.llm.client import chat_json

MAX_QUESTIONS = 59
MIN_SLIDE_WORDS = 15  # skip title/footer-only slides

QUESTION_SYSTEM = (
    "You are a senior medical exam writer creating NBME/USMLE-style single-best-answer "
    "(SBA) questions for a first-year medical school block exam. You write second- and "
    "third-order reasoning questions that test applied foundational science, never course "
    "logistics. Rules:\n"
    "1. The question stem is a STANDALONE clinical vignette or mechanism-based prompt. The "
    "student sees ONLY the stem - never the slide, its image, the lecture title, or lecture "
    "number. Never reference 'this slide', 'the slide', 'the image above', 'this lecture', or "
    "'as discussed'. The stem must be self-contained and answerable on its own.\n"
    "2. Write board-style application questions: a short clinical scenario or a "
    "'predict / explain / interpret' prompt that requires 2nd-3rd order reasoning (apply a "
    "mechanism, interpret a finding, link a concept to an outcome). Avoid trivial one-step "
    "recall of isolated bullet points.\n"
    "3. Anchored to the source slide: the correct answer must be directly supported by the "
    "biomedical content on the slide. Do not invent facts absent from the slide. Distractors "
    "must be plausible but clearly wrong given that content.\n"
    "4. IGNORE all non-examinable slide material: course objectives, textbook "
    "recommendations, syllabi, schedules, logistics, instructor names, grading, or 'how to "
    "study'. Never write a question about the course or its materials themselves.\n"
    "5. Every question must be exam-relevant foundational science (anatomy, physiology, "
    "biochemistry, cell biology, pathology, pharmacology, microbiology, etc.).\n"
    "6. Exactly one answer is unambiguously best."
)

EXAMPLE_QUESTIONS = """EXAMPLE STYLE - board-style, self-contained stems, single best answer, 5 options A-E:

Q. A 24-year-old primigravida is diagnosed with a primary cytomegalovirus infection at 8 weeks' gestation. Which statement best predicts the consequence for the fetus?
A. The virus cannot cross the placenta, so fetal infection will not occur
B. The virus crosses the placenta and can cause fetal infection
C. Crossing is limited to small molecules; viruses are too large to pass
D. Crossing occurs only after 32 weeks' gestation
E. Crossing requires concurrent maternal bacteremia

Q. During implantation, endometrial stromal cells enlarge, accumulate glycogen and lipids, become polyhedral, and eventually undergo apoptosis. Which process do these changes represent, and what is its primary role?
A. Zonal reaction - prevents polyspermy
B. Cortical reaction - blocks additional sperm entry
C. Decidual reaction - supports and protects the implanting embryo
D. Acrosome reaction - enables penetration of the zona pellucida
E. Inflammatory reaction - rejects the embryo

Q. A patient with chronic kidney disease has impaired renal activation of vitamin D. Which downstream effect is most likely to develop as a direct consequence?
A. Increased intestinal calcium absorption
B. Increased bone mineralization
C. Decreased serum phosphate
D. Decreased intestinal calcium absorption
E. Increased renal calcium reabsorption
"""

BATCH_SIZE = 8  # slides per API call in batched generation

QUESTION_BATCH_PROMPT = """Write NBME/USMLE-style single-best-answer questions for a medical student.

You are given {count} slides. Return EXACTLY {count} questions - on average one per slide.

Each slide is presented as:
SLIDE <index> | Lecture: <lecture title> | Slide number: <slide_num>
<slide text + image caption>

SOURCE MATERIAL is for you only - the student never sees it.

RULES:
1. A slide is NON-TESTABLE if it contains only a thank-you message, "Questions?", a title or lecturer header, learning objectives, a schedule, a references list, a pure summary/review/overview recap with no new facts (e.g. a slide titled "Summary" or "Review"), or other administrative content with no biomedical facts. Do NOT write a question for a non-testable slide.
2. To still return exactly {count} questions, when you skip a non-testable slide write an ADDITIONAL question anchored to another TESTABLE slide in this batch instead. A slide may be the source of more than one question, so slide_index may repeat. Every question must be strictly based on the slide it is anchored to.
3. Backfill anchors MUST be genuinely testable content slides (slides with concrete biomedical facts). Summary, review, thank-you, and "Questions?" slides are non-testable and must NEVER be used as anchors, even as a last resort. If no testable slide is available to anchor an extra question, return fewer than {count} questions rather than writing a weak, meta, or summary-style question.
4. Each question must be board-style and self-contained: a clinical vignette or mechanism prompt requiring 2nd-3rd order reasoning. Never reference "this slide", "the slide above", the lecture, or any unseen material.
5. Do not name specific diseases, drugs, enzymes, or proteins that are not explicitly named in the slide the question is anchored to.

SLIDES:
{slides}

EXAM STYLE REFERENCE:
{examples}

Return ONLY JSON:
{{
  "questions": [
    {{
      "slide_index": 0,
      "question": "Self-contained stem. Do NOT include answer letters or reference unseen material.",
      "options": ["A", "B", "C", "D", "E"],
      "correct_index": 0,
      "explanation": "Teaching explanation, 2-5 sentences: why correct is right, why the strongest distractors are wrong, and the high-yield takeaway."
    }}
  ]
}}

- slide_index must be an index from 0 to {count_minus_1} (the slide's position in this batch); it may repeat.
- Exactly 5 options per question. correct_index must be 0-4."""


def _strip_letter_prefix(text):
    stripped = re.sub(r"^[A-E]\.\s*", "", text.strip())
    return stripped.strip()


# Stems that reference unseen material or course logistics are rejected so students
# are never asked questions about slides/images/lectures they cannot see.
_FORBIDDEN_STEM = re.compile(
    r"\b(this slide|the slide|the image above|the figure above|the diagram above|"
    r"this lecture|the lecture|this module|the module|as discussed|as shown|"
    r"textbook|course objective|learning objective|syllabus|grading|"
    r"this course|our course|how to study|the exam will cover|"
    r"summariz\w*|recap\b|reviewing (the|this|our))\b",
    re.IGNORECASE,
)


def _valid_stem(stem):
    if not stem or not stem.strip():
        return False
    if _FORBIDDEN_STEM.search(stem):
        return False
    return True


SLIDE_WEIGHT_ALPHA = 0.6  # how strongly low-question slides are preferred over randomness


def _slide_weights(pool):
    """Weight each slide inversely by how many questions have been generated from it.
    weight = 1 / (1 + q_count) ** alpha. Slides with no questions are most likely;
    nothing is ever excluded, so selection stays unpredictable."""
    if not pool:
        return [], []
    conn = get_conn()
    ph = ",".join("?" * len(pool))
    rows = conn.execute(
        f"SELECT slide_id, COUNT(*) c FROM questions "
        f"WHERE slide_id IN ({ph}) GROUP BY slide_id",
        tuple(s["slide_id"] for s in pool),
    ).fetchall()
    conn.close()
    counts = {r["slide_id"]: r["c"] for r in rows}
    weights = []
    for s in pool:
        q = counts.get(s["slide_id"], 0)
        weights.append(1.0 / ((1 + q) ** SLIDE_WEIGHT_ALPHA))
    return pool, weights


def _weighted_sample_without_replacement(pool, weights, k):
    """Sample k distinct items, each picked with probability proportional to its
    weight (re-weighted after each pick). Preserves low-question bias + randomness."""
    pool = list(pool)
    weights = list(weights)
    out = []
    for _ in range(min(k, len(pool))):
        total = sum(weights)
        if total <= 0:
            break
        r = random.random() * total
        cum = 0.0
        for i, w in enumerate(weights):
            cum += w
            if r <= cum:
                out.append(pool.pop(i))
                weights.pop(i)
                break
    return out


def select_slides(lecture_ids=None, target=MAX_QUESTIONS, exclude_slide_ids=None):
    """Select slides across chosen lectures (or all), spreading evenly across
    lectures while biasing toward slides with the fewest generated questions.
    A slide is eligible if it has extractable text OR at least one page image
    (the vision model reads the image directly).
    Returns list of {slide_id, lecture_id, lecture_title, slide_num, text, caption}.
    exclude_slide_ids: slide ids to skip (used for top-up after skips/shortfalls)."""
    exclude = set(exclude_slide_ids or [])
    conn = get_conn()
    if lecture_ids:
        ph = ",".join("?" * len(lecture_ids))
        lectures = conn.execute(
            f"SELECT id, title FROM lectures WHERE id IN ({ph}) ORDER BY id",
            tuple(lecture_ids),
        ).fetchall()
    else:
        lectures = conn.execute("SELECT id, title FROM lectures ORDER BY id").fetchall()

    pool = []
    for l in lectures:
        rows = conn.execute(
            "SELECT s.id, s.slide_num, s.text, s.caption FROM slides s "
            "WHERE s.lecture_id=? AND (length(trim(s.text)) >= ? "
            "OR EXISTS (SELECT 1 FROM slide_images si WHERE si.slide_id=s.id)) "
            "ORDER BY s.slide_num",
            (l["id"], MIN_SLIDE_WORDS),
        ).fetchall()
        for r in rows:
            if r["id"] in exclude:
                continue
            pool.append(
                {
                    "slide_id": r["id"],
                    "lecture_id": l["id"],
                    "lecture_title": l["title"],
                    "slide_num": r["slide_num"],
                    "text": r["text"] or "",
                    "caption": r["caption"] or "",
                }
            )
    conn.close()

    if not pool:
        return []

    # Attach absolute image paths (page renders) so vision question-gen can read them.
    conn = get_conn()
    ph = ",".join("?" * len(pool))
    img_rows = conn.execute(
        f"SELECT slide_id, path FROM slide_images WHERE slide_id IN ({ph}) ORDER BY seq",
        tuple(s["slide_id"] for s in pool),
    ).fetchall()
    conn.close()
    from app.ingest.images import IMAGES_ROOT
    _VISION_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
    imgs_by_slide = {}
    for r in img_rows:
        if os.path.splitext(r["path"])[1].lower() not in _VISION_IMAGE_EXTS:
            continue
        imgs_by_slide.setdefault(r["slide_id"], []).append(
            os.path.join(IMAGES_ROOT, r["path"])
        )
    for s in pool:
        s["image_paths"] = imgs_by_slide.get(s["slide_id"], [])

    # Per-lecture, weighted-sample so under-covered slides come first, then
    # round-robin across lectures so every chosen lecture is represented.
    by_lecture = {}
    for s in pool:
        by_lecture.setdefault(s["lecture_id"], []).append(s)

    keys = list(by_lecture.keys())
    random.shuffle(keys)
    lecture_lists = {}
    for k in keys:
        items, weights = _slide_weights(by_lecture[k])
        lecture_lists[k] = _weighted_sample_without_replacement(items, weights, target)

    selected = []
    idx = 0
    while len(selected) < target and keys:
        progressed = False
        for k in keys:
            if len(selected) >= target:
                break
            lst = lecture_lists[k]
            if idx < len(lst):
                selected.append(lst[idx])
                progressed = True
        idx += 1
        if not progressed:
            break
    return selected[:target]


def _question_from_result(s, result):
    """Validate/normalize one returned question against its source slide `s`.
    Returns a question dict ready to store, or None if malformed."""
    options = [_strip_letter_prefix(o) for o in result.get("options", [])]
    if len(options) != 5:
        return None
    try:
        correct = int(result.get("correct_index", 0))
    except (ValueError, TypeError):
        return None
    if not (0 <= correct < 5):
        return None
    stem = (result.get("question") or "").strip()
    if not _valid_stem(stem) or any(_FORBIDDEN_STEM.search(o) for o in options):
        return None
    return {
        "lecture_id": s["lecture_id"],
        "slide_id": s["slide_id"],
        "slide_num": s["slide_num"],
        "question": stem,
        "options": options,
        "correct_index": correct,
        "explanation": (result.get("explanation") or "").strip(),
    }


def _build_batch_prompt(batch):
    """Build one prompt covering `batch` slides. Images are attached separately
    (vision mode) so the model reads each slide's page image directly."""
    blocks = []
    for i, s in enumerate(batch):
        slide_text = (s["text"] or "").strip()
        caption = (s["caption"] or "").strip()
        img_note = ""
        if s.get("image_paths"):
            img_note = f"\n[Attached image: page image for this slide]"
        if caption:
            img_note += f"\n[Image caption]: {caption}"
        blocks.append(
            f"SLIDE {i} | Lecture: {s['lecture_title']} | Slide number: {s['slide_num']}\n"
            f"{slide_text}{img_note}"
        )
    head = QUESTION_BATCH_PROMPT.format(
        count=len(batch),
        count_minus_1=len(batch) - 1,
        slides="\n\n".join(blocks),
        examples=EXAMPLE_QUESTIONS,
    )
    return head


_STOP_WORDS = set(
    "a an the of to in for on and or is are was were be been being with from by at "
    "as it its this that these those which who whom what when where how does do did "
    "can could will would should may might not no yes have has had most likely best "
    "patient woman man child researcher study".split()
)


def _content_words(text):
    return {w for w in re.findall(r"[a-z]{4,}", (text or "").lower()) if w not in _STOP_WORDS}


def _best_slide_index(qtext, batch, declared):
    """Re-anchor a returned question to the slide it best matches by content overlap.
    Falls back to the model's declared slide_index if no slide shares any words
    (avoids dropping questions). Fixes mislabeled indexes (e.g. a content question
    tagged to a thank-you slide)."""
    qwords = _content_words(qtext)
    if not qwords:
        return declared
    scores = [
        len(qwords & _content_words(s["text"] + " " + (s["caption"] or "")))
        for s in batch
    ]
    best = max(range(len(batch)), key=lambda i: scores[i])
    return declared if scores[best] == 0 else best


def _gen_batch(batch):
    """Generate questions for a single batch of slides in one vision API call.
    Returns a list of valid question dicts (may be fewer than the batch size)."""
    if not batch:
        return []
    prompt = _build_batch_prompt(batch)
    images = []
    for s in batch:
        images.extend(s.get("image_paths") or [])
    result = chat_json(
        prompt,
        system=QUESTION_SYSTEM,
        temperature=0.7,
        max_tokens=min(8000, 500 + 700 * len(batch)),
        images=images or None,
    )
    out = []
    for q in result.get("questions") or []:
        try:
            declared = int(q.get("slide_index", -1))
        except (ValueError, TypeError):
            continue
        if not (0 <= declared < len(batch)):
            continue
        qtext = " ".join(filter(None, [q.get("question", ""), *(q.get("options") or []), q.get("explanation", "")]))
        idx = _best_slide_index(qtext, batch, declared)
        item = _question_from_result(batch[idx], q)
        if item:
            out.append(item)
    return out


def generate_questions_for_slides(slides, batch_size=BATCH_SIZE, max_workers=4):
    """Generate questions for slides in concurrent batched API calls.
    Returns list of dicts ready to store (slide-anchored)."""
    batches = [slides[i:i + batch_size] for i in range(0, len(slides), batch_size)]
    out = []
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = [ex.submit(_gen_batch, b) for b in batches]
        for fut in as_completed(futures):
            try:
                out.extend(fut.result())
            except Exception as e:
                print(f"batch question generation failed: {e}")
    return out


QUIZ_SECONDS_PER_QUESTION = 90  # 1.5 min per question in quiz mode


def quiz_time_limit_min(question_count):
    """Total minutes for a quiz session: 1.5 min per question, rounded up."""
    import math
    return max(1, math.ceil(QUIZ_SECONDS_PER_QUESTION * question_count / 60))


def create_practice_session(lecture_ids=None, target=MAX_QUESTIONS, time_mode="tutor"):
    """Create a practice session: pick slides, generate fresh questions, store them.
    Returns (session_id, question_count). lecture_ids: list of lectures to draw
    from (None/empty = all)."""
    if target > MAX_QUESTIONS:
        target = MAX_QUESTIONS

    used_ids = []
    questions = []
    rounds = 0
    while len(questions) < target and rounds < 4:
        rounds += 1
        need = target - len(questions)
        slides = select_slides(lecture_ids=lecture_ids, target=need, exclude_slide_ids=used_ids)
        if not slides:
            break
        used_ids.extend(s["slide_id"] for s in slides)
        questions.extend(generate_questions_for_slides(slides))

    # Trim any overshoot (e.g. backfill produced extra anchored questions).
    if len(questions) > target:
        random.shuffle(questions)
        questions = questions[:target]

    tutor = time_mode != "quiz"
    time_limit = 0 if tutor else quiz_time_limit_min(len(questions))
    first_lecture = lecture_ids[0] if lecture_ids else None

    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO sessions(title, status, mode, target_count, lecture_id, gen_status, "
        "tutor_mode, time_limit_min) VALUES(?, 'active', 'practice', ?, ?, 'done', ?, ?)",
        (f"Practice ({len(questions)} questions)", len(questions), first_lecture,
         tutor, time_limit),
    )
    sid = cur.lastrowid
    random.shuffle(questions)
    for i, q in enumerate(questions):
        cur2 = conn.execute(
            "INSERT INTO questions(lecture_id, slide_id, question, options, "
            "correct_index, explanation, level, source) VALUES(?,?,?,?,?,?,?, 'session')",
            (
                q["lecture_id"],
                q["slide_id"],
                q["question"],
                json.dumps(q["options"]),
                q["correct_index"],
                q["explanation"],
                "recall",
            ),
        )
        qid = cur2.lastrowid
        conn.execute(
            "INSERT INTO session_questions(session_id, question_id, position) VALUES(?,?,?)",
            (sid, qid, i),
        )
    conn.commit()
    conn.close()
    return sid, len(questions)


def create_review_session(lecture_ids=None, time_mode="tutor"):
    """Create a review session from unresolved missed questions.
    lecture_ids: restrict to the given lectures (None/empty = all)."""
    conn = get_conn()
    if lecture_ids:
        ph = ",".join("?" * len(lecture_ids))
        rows = conn.execute(
            f"SELECT DISTINCT question_id FROM missed WHERE resolved=0 AND lecture_id IN ({ph}) "
            "ORDER BY missed_at", tuple(lecture_ids),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT DISTINCT question_id FROM missed WHERE resolved=0 ORDER BY missed_at"
        ).fetchall()
    qids = [r["question_id"] for r in rows]
    if not qids:
        conn.close()
        return None, 0

    tutor = time_mode != "quiz"
    time_limit = 0 if tutor else quiz_time_limit_min(len(qids))
    first_lecture = lecture_ids[0] if lecture_ids else None

    cur = conn.execute(
        "INSERT INTO sessions(title, status, mode, target_count, lecture_id, gen_status, "
        "tutor_mode, time_limit_min) VALUES(?, 'active', 'review', ?, ?, 'done', ?, ?)",
        (f"Review ({len(qids)} missed)", len(qids), first_lecture, tutor, time_limit),
    )
    sid = cur.lastrowid
    for i, qid in enumerate(qids):
        conn.execute(
            "INSERT INTO session_questions(session_id, question_id, position) VALUES(?,?,?)",
            (sid, qid, i),
        )
    conn.commit()
    conn.close()
    return sid, len(qids)


def missed_count():
    conn = get_conn()
    c = conn.execute("SELECT COUNT(*) c FROM missed WHERE resolved=0").fetchone()["c"]
    conn.close()
    return c


def missed_by_lecture():
    conn = get_conn()
    rows = conn.execute(
        "SELECT m.lecture_id, l.title, COUNT(*) c FROM missed m "
        "JOIN lectures l ON l.id=m.lecture_id WHERE m.resolved=0 "
        "GROUP BY m.lecture_id ORDER BY c DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
