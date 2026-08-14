"""Backfill: convert all existing PPTX lectures to PDF and re-extract in place.

For each lecture with source='pptx':
  1. Convert lectures/<filename> -> lectures/<name>.pdf via PowerPoint AppleScript.
  2. Re-extract slide text from the PDF and update slides in place (by slide_num).
  3. Clear slide_images and re-render full-page images.
  4. Update lectures.filename/source to the PDF.
  5. Optionally regenerate captions + summary (vision).

Lecture/slide IDs, questions, sessions, answers, and missed history are preserved.

Usage:
  .venv/bin/python -m app.ingest.backfill [--captions] [--summary]
"""
import os
import sys

from app.db import get_conn, DB_PATH
from app.ingest.images import IMAGES_ROOT

LECTURES_DIR = os.path.join(os.path.dirname(DB_PATH), "..", "lectures")


def backfill_lecture(lid, filename, do_captions=True, do_summary=True):
    """Convert + re-extract one lecture in place. Returns (slides, images) or raises."""
    from app.ingest.convert import pptx_to_pdf
    from app.ingest.slides import extract_pdf
    from app.ingest.images import extract_pdf_images

    src = os.path.join(LECTURES_DIR, filename)
    if not os.path.exists(src):
        raise FileNotFoundError(f"missing source file: {src}")

    # Convert pptx -> pdf next to it
    pdf_path = pptx_to_pdf(src)
    pdf_name = os.path.basename(pdf_path)

    # Re-extract text
    slides = extract_pdf(pdf_path)
    conn = get_conn()
    conn.execute(
        "UPDATE lectures SET filename=?, source='pdf', title=? WHERE id=?",
        (pdf_name, os.path.splitext(pdf_name)[0], lid),
    )
    # Match slides by slide_num and rewrite text in place
    by_num = {num: text for num, text in slides}
    rows = conn.execute(
        "SELECT id, slide_num FROM slides WHERE lecture_id=? ORDER BY slide_num",
        (lid,),
    ).fetchall()
    word_count = 0
    for r in rows:
        text = by_num.get(r["slide_num"], "")
        conn.execute("UPDATE slides SET text=? WHERE id=?", (text, r["id"]))
        word_count += len(text.split())
    conn.execute(
        "UPDATE lectures SET slide_count=?, word_count=? WHERE id=?",
        (len(rows), word_count, lid),
    )

    # Re-render full-page images
    _clear_slide_images(conn, lid)
    conn.commit()
    count = extract_pdf_images(lid, pdf_path, conn=conn)
    conn.commit()
    conn.close()

    # Captions + summary (vision)
    if do_captions:
        try:
            from app.llm.captions import generate_captions_for_lecture
            generate_captions_for_lecture(lid, force=True)
        except Exception as e:
            print(f"  captions failed for lecture {lid}: {e}")
    if do_summary:
        try:
            from app.llm.summaries import generate_summary
            generate_summary(lid)
        except Exception as e:
            print(f"  summary failed for lecture {lid}: {e}")
    return len(rows), count


def _clear_slide_images(conn, lecture_id):
    conn.execute(
        "DELETE FROM slide_images WHERE slide_id IN "
        "(SELECT id FROM slides WHERE lecture_id=?)",
        (lecture_id,),
    )
    d = os.path.join(IMAGES_ROOT, str(lecture_id))
    if os.path.isdir(d):
        for f in os.listdir(d):
            try:
                os.remove(os.path.join(d, f))
            except OSError:
                pass


def main():
    do_captions = "--captions" in sys.argv
    do_summary = "--summary" in sys.argv
    conn = get_conn()
    lectures = conn.execute(
        "SELECT id, title, filename FROM lectures WHERE source='pptx' ORDER BY id"
    ).fetchall()
    conn.close()
    print(f"Found {len(lectures)} pptx lectures to backfill.")
    ok = 0
    skipped = []
    for l in lectures:
        print(f"[{l['id']}] {l['title']}")
        try:
            slides, images = backfill_lecture(
                l["id"], l["filename"],
                do_captions=do_captions, do_summary=do_summary,
            )
            print(f"  -> converted; {slides} slides, {images} images re-rendered")
            ok += 1
        except Exception as e:
            print(f"  !! skipped: {e}")
            skipped.append((l["id"], l["filename"]))
    print(f"\nDone: {ok} backfilled, {len(skipped)} skipped.")
    for lid, f in skipped:
        print(f"  skipped: lecture {lid} ({f})")


if __name__ == "__main__":
    main()
