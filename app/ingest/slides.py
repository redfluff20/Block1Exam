import os
import re
import fitz

from app.db import get_conn


def extract_pdf(path):
    doc = fitz.open(path)
    pages = []
    for i, page in enumerate(doc):
        text = page.get_text("text")
        text = re.sub(r"\s+", " ", text).strip()
        pages.append((i + 1, text))
    doc.close()
    return pages


def import_lecture(path):
    ext = os.path.splitext(path)[1].lower()
    base = os.path.basename(path)
    title = os.path.splitext(base)[0]

    if ext != ".pdf":
        from app.ingest.convert import pptx_to_pdf
        path = pptx_to_pdf(path)
        base = os.path.basename(path)
        title = os.path.splitext(base)[0]

    # Slow extraction first, before touching the DB, so no lock is held during it.
    slides = extract_pdf(path)

    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO lectures(title, filename, source) VALUES(?,?,?)",
        (title, base, "pdf"),
    )
    lecture_id = cur.lastrowid

    word_count = 0
    for num, text in slides:
        conn.execute(
            "INSERT INTO slides(lecture_id, slide_num, text) VALUES(?,?,?)",
            (lecture_id, num, text),
        )
        word_count += len(text.split())
    conn.execute(
        "UPDATE lectures SET slide_count=?, word_count=? WHERE id=?",
        (len(slides), word_count, lecture_id),
    )
    conn.commit()
    conn.close()

    # Extract full-page images after slides exist (best-effort; never blocks import)
    try:
        from app.ingest.images import extract_lecture_images
        extract_lecture_images(lecture_id)
    except Exception as e:
        print(f"image extraction for lecture {lecture_id} failed: {e}")

    return lecture_id


def list_lectures():
    conn = get_conn()
    rows = conn.execute(
        "SELECT l.*, "
        "(SELECT COUNT(*) FROM slides s WHERE s.lecture_id=l.id "
        "  AND length(trim(s.caption)) > 0) AS captioned_slides "
        "FROM lectures l ORDER BY l.created_at DESC"
    ).fetchall()
    conn.close()
    return rows
