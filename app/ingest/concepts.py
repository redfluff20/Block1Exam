from app.db import get_conn


def coverage_stats():
    """Slide-based coverage: how many content slides have at least one question."""
    conn = get_conn()
    total = conn.execute(
        "SELECT COUNT(*) c FROM slides WHERE length(trim(text)) >= 15"
    ).fetchone()["c"]
    covered = conn.execute(
        "SELECT COUNT(DISTINCT q.slide_id) c FROM questions q WHERE q.slide_id IS NOT NULL"
    ).fetchone()["c"]
    conn.close()
    return {
        "total": total,
        "covered": covered,
        "question_coverage": round(100 * covered / total, 1) if total else 0,
    }
