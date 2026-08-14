"""Duplicate lecture detection.

Computes a lightweight fingerprint of a lecture's slide text and flags a new
lecture as a duplicate if it overlaps heavily with an existing one.
"""
import re
import os

from app.db import get_conn, DB_PATH

LECTURES_DIR = os.path.join(os.path.dirname(DB_PATH), "..", "lectures")


def _tokens(text):
    """Extract normalized content tokens, dropping common filler/boilerplate."""
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    stop = {
        "the", "a", "an", "and", "or", "of", "to", "in", "for", "on", "with",
        "is", "are", "was", "were", "be", "this", "that", "these", "those",
        "by", "as", "at", "from", "it", "its", "their", "his", "her",
        "professor", "md", "phd", "dr", "med", "school", "uthealth", "mcgov",
        "university", "tx", "houston", "texas", "2026", "2025", "fall", "spring",
        "slide", "copyright", "lecture", "objectives", "will", "we", "you",
    }
    return {t for t in tokens if t not in stop and len(t) > 2}


def _lecture_text(lecture_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT text FROM slides WHERE lecture_id=?",
        (lecture_id,),
    ).fetchall()
    conn.close()
    return " ".join((r["text"] or "") for r in rows)


def _jaccard(a, b):
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def detect_duplicate(text, threshold=0.55, lecture_ids=None):
    """Given raw slide text of a candidate lecture, return the id+title of an
    existing lecture it duplicates, or None."""
    cand = _tokens(text)
    conn = get_conn()
    if lecture_ids is None:
        lectures = conn.execute("SELECT id, title FROM lectures").fetchall()
    else:
        ph = ",".join("?" * len(lecture_ids))
        lectures = conn.execute(
            f"SELECT id, title FROM lectures WHERE id IN ({ph})", tuple(lecture_ids)
        ).fetchall()
    conn.close()

    best = None
    best_score = 0.0
    for l in lectures:
        existing = _tokens(_lecture_text(l["id"]))
        score = _jaccard(cand, existing)
        if score > best_score:
            best_score = score
            best = l
    if best and best_score >= threshold:
        return {"lecture_id": best["id"], "title": best["title"], "score": round(best_score, 3)}
    return None


def detect_duplicate_file(path, lecture_ids=None):
    """Detect duplicate from a file path (extracts text fresh, text-only compare)."""
    from app.ingest.slides import extract_pdf
    ext = os.path.splitext(path)[1].lower()
    if ext != ".pdf":
        from app.ingest.convert import pptx_to_pdf
        path = pptx_to_pdf(path)
    slides = extract_pdf(path)
    text = " ".join(t for _, t in slides)
    return detect_duplicate(text, lecture_ids=lecture_ids)
