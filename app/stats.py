"""Slide-based progress stats.

The slide is the atomic unit. Every question is anchored to a slide, so
coverage = which slides have at least one generated question, and accuracy is
computed from the `answers` table (which records each answer's correctness).
"""
from app.db import get_conn

MIN_SLIDE_WORDS = 15


def lecture_stats():
    """Per-lecture aggregation of slide coverage + accuracy."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT l.id AS lecture_id, l.title AS lecture_title, "
        "COUNT(DISTINCT s.id) AS slides_total, "
        "COUNT(DISTINCT CASE WHEN q.id IS NOT NULL THEN s.id END) AS slides_covered, "
        "COUNT(DISTINCT q.id) AS q_count, "
        "COUNT(DISTINCT CASE WHEN a.id IS NOT NULL THEN q.id END) AS q_answered, "
        "SUM(CASE WHEN a.correct=1 THEN 1 ELSE 0 END) AS correct, "
        "COUNT(a.id) AS answers "
        "FROM lectures l "
        "JOIN slides s ON s.lecture_id=l.id "
        "AND length(trim(s.text)) >= ? "
        "LEFT JOIN questions q ON q.slide_id=s.id "
        "LEFT JOIN answers a ON a.question_id=q.id "
        "GROUP BY l.id ORDER BY l.id",
        (MIN_SLIDE_WORDS,),
    ).fetchall()
    conn.close()

    out = []
    for r in rows:
        d = dict(r)
        d["slides_covered"] = d["slides_covered"] or 0
        d["q_count"] = d["q_count"] or 0
        d["q_answered"] = d["q_answered"] or 0
        d["correct"] = d["correct"] or 0
        d["answers"] = d["answers"] or 0
        d["coverage_pct"] = (
            round(100 * d["slides_covered"] / d["slides_total"], 1)
            if d["slides_total"]
            else 0
        )
        d["accuracy"] = (
            round(d["correct"] / d["answers"], 2) if d["answers"] else None
        )
        out.append(d)
    return out


def weak_slides(limit=15, max_accuracy=0.85, min_answers=1):
    """Slides with the worst answer accuracy (only genuinely weak ones)."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT s.id AS slide_id, s.slide_num, s.text, l.title AS lecture_title, "
        "l.id AS lecture_id, "
        "COUNT(a.id) AS answers, "
        "SUM(CASE WHEN a.correct=1 THEN 1 ELSE 0 END) AS correct "
        "FROM slides s "
        "JOIN lectures l ON l.id=s.lecture_id "
        "JOIN questions q ON q.slide_id=s.id "
        "JOIN answers a ON a.question_id=q.id "
        "GROUP BY s.id HAVING answers >= ? "
        "ORDER BY correct*1.0/answers ASC LIMIT ?",
        (min_answers, limit),
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        d["accuracy"] = round(d["correct"] / d["answers"], 2) if d["answers"] else None
        out.append(d)
    return out


def gaps():
    """Slides with no questions yet (coverage gaps)."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT s.id AS slide_id, s.slide_num, s.text, l.title AS lecture_title, "
        "l.id AS lecture_id "
        "FROM slides s JOIN lectures l ON l.id=s.lecture_id "
        "WHERE length(trim(s.text)) >= ? "
        "AND NOT EXISTS (SELECT 1 FROM questions q WHERE q.slide_id=s.id) "
        "ORDER BY l.id, s.slide_num LIMIT 50",
        (MIN_SLIDE_WORDS,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
