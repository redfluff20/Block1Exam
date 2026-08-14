import os
import json
from datetime import datetime, timedelta

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.db import init_db, get_conn, DB_PATH
from app.ingest.slides import import_lecture, list_lectures
from app.ingest.concepts import coverage_stats
from app.llm.summaries import generate_summary, get_summary

init_db()

app = FastAPI(title="Block1 Exam Prep")

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

from app.ingest.images import IMAGES_ROOT as _IMAGES_ROOT
os.makedirs(_IMAGES_ROOT, exist_ok=True)
app.mount("/images", StaticFiles(directory=_IMAGES_ROOT), name="images")


# ---------- Static ----------
def _asset_version():
    """Cache-buster derived from asset file mtimes, so CSS and JS always share
    one version and change together on every origin."""
    js = os.path.getmtime(os.path.join(STATIC_DIR, "app.js"))
    css = os.path.getmtime(os.path.join(STATIC_DIR, "style.css"))
    return str(int(max(js, css)))


@app.get("/")
def index():
    with open(os.path.join(STATIC_DIR, "index.html"), encoding="utf-8") as f:
        html = f.read()
    html = html.replace("__CACHE_VERSION__", _asset_version())
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


# ---------- Settings ----------
@app.get("/api/settings/llm")
def api_settings_llm():
    """Return LLM config status (never the key itself)."""
    from app.db import get_setting
    from app.llm.client import get_model, get_base_url
    key = get_setting("llm_api_key")
    return {
        "api_key_set": bool(key),
        "model": get_model(),
        "base_url": get_base_url(),
    }


class LLMSettings(BaseModel):
    api_key: str
    base_url: str = None
    model: str = None


@app.post("/api/settings/llm")
def api_save_settings_llm(body: LLMSettings):
    """Save the LLM API key (+ optional base URL / model) to the local DB."""
    from app.db import set_setting
    key = (body.api_key or "").strip()
    if not key:
        raise HTTPException(400, "API key cannot be empty")
    set_setting("llm_api_key", key)
    if body.base_url is not None:
        set_setting("llm_base_url", body.base_url.strip())
    if body.model is not None:
        set_setting("llm_model", body.model.strip())
    return {"saved": True}


# ---------- Lectures ----------
@app.get("/api/lectures")
def api_lectures():
    lectures = list_lectures()
    out = []
    for l in lectures:
        out.append(dict(l))
    return out


@app.post("/api/lectures/import")
async def api_import_lectures(files: list[UploadFile] = File(...)):
    imported = []
    errors = []
    duplicates = []
    upload_dir = os.path.join(os.path.dirname(DB_PATH), "..", "lectures")
    os.makedirs(upload_dir, exist_ok=True)
    for f in files:
        ext = os.path.splitext(f.filename or "")[1].lower()
        if ext not in (".pdf", ".pptx"):
            errors.append(f"{f.filename}: unsupported (use .pdf or .pptx)")
            continue
        dest = os.path.join(upload_dir, f.filename)
        with open(dest, "wb") as out:
            content = await f.read()
            out.write(content)
        try:
            # Detect duplicates before importing
            from app.ingest.duplicates import detect_duplicate_file
            dup = detect_duplicate_file(dest)
            if dup:
                # remove the temp file we just saved
                try:
                    os.remove(dest)
                except OSError:
                    pass
                duplicates.append(
                    {"filename": f.filename, "dup_of": dup["lecture_id"], "title": dup["title"], "score": dup["score"]}
                )
                continue
            lid = import_lecture(dest)
            imported.append(lid)
            # Auto-pipeline: captions + summary run in the background; the Learn
            # list shows live status until "summary ready".
            import threading
            from app.llm.captions import generate_captions_for_lecture
            from app.llm.summaries import generate_summary

            def _run(lid):
                try:
                    generate_captions_for_lecture(lid, force=True)
                except Exception as e:
                    print(f"auto-captions failed for lecture {lid}: {e}")
                try:
                    generate_summary(lid)
                except Exception as e:
                    print(f"auto-summary failed for lecture {lid}: {e}")

            threading.Thread(target=_run, args=(lid,), daemon=True).start()
        except Exception as e:
            errors.append(f"{f.filename}: {e}")
    return {"imported": imported, "errors": errors, "duplicates": duplicates}


@app.get("/api/lectures/{lid}/deck")
def api_lecture_deck(lid: int):
    """Slides with their text AND images, for the inline slide-by-slide view."""
    from app.ingest.images import images_for_slides
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM slides WHERE lecture_id=? ORDER BY slide_num", (lid,)
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        d["images"] = images_for_slides([d["id"]])
        out.append(d)
    return out


@app.delete("/api/lectures/{lid}")
def api_delete_lecture(lid: int):
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM lectures WHERE id=?", (lid,)
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Lecture not found")
    # Remove everything belonging to this lecture (FK cascades handle the rest)
    conn.execute("DELETE FROM lectures WHERE id=?", (lid,))
    conn.commit()
    conn.close()
    return {"deleted": lid}


@app.post("/api/lectures/{lid}/regenerate")
def api_regenerate_lecture(lid: int):
    """Re-run captions + summary for one lecture in the background (vision)."""
    conn = get_conn()
    row = conn.execute("SELECT id FROM lectures WHERE id=?", (lid,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Lecture not found")
    conn.execute(
        "UPDATE lectures SET summary_status='generating' WHERE id=?", (lid,)
    )
    conn.commit()
    conn.close()

    import threading
    from app.llm.captions import generate_captions_for_lecture
    from app.llm.summaries import generate_summary

    def _run(lid):
        try:
            generate_captions_for_lecture(lid, force=True)
        except Exception as e:
            print(f"regen captions failed for lecture {lid}: {e}")
        try:
            generate_summary(lid)
        except Exception as e:
            print(f"regen summary failed for lecture {lid}: {e}")

    threading.Thread(target=_run, args=(lid,), daemon=True).start()
    return {"status": "started"}


# ---------- Summaries ----------
@app.get("/api/lectures/{lid}/summary")
def api_summary(lid: int):
    s = get_summary(lid)
    if not s:
        conn = get_conn()
        st = conn.execute(
            "SELECT summary_status FROM lectures WHERE id=?", (lid,)
        ).fetchone()
        conn.close()
        return {"status": st["summary_status"] if st else "not_started", "summary": None}
    return {"status": "done", "summary": dict(s)}


@app.post("/api/lectures/{lid}/summary/generate")
def api_generate_summary(lid: int):
    try:
        result = generate_summary(lid)
        return {"status": "done", "key_points": len(result.get("key_points", []))}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# ---------- Questions ----------
@app.get("/api/lectures/{lid}/questions")
def api_lecture_questions(lid: int, level: str = None):
    conn = get_conn()
    if level:
        rows = conn.execute(
            "SELECT * FROM questions WHERE lecture_id=? AND level=?",
            (lid, level),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM questions WHERE lecture_id=?", (lid,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------- Dashboard ----------
@app.get("/api/lectures/{lid}/slides_progress")
def api_lecture_slides_progress(lid: int):
    """Per-slide progress for a lecture: slide coordinate + questions + accuracy."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT s.id AS slide_id, s.slide_num, s.text, "
        "(SELECT COUNT(*) FROM questions q WHERE q.slide_id=s.id) AS q_count, "
        "(SELECT COUNT(*) FROM questions q JOIN answers a ON a.question_id=q.id "
        "  WHERE q.slide_id=s.id AND a.correct=1) AS correct_count, "
        "(SELECT COUNT(*) FROM questions q JOIN answers a ON a.question_id=q.id "
        "  WHERE q.slide_id=s.id AND a.correct=0) AS wrong_count "
        "FROM slides s WHERE s.lecture_id=? "
        "AND length(trim(s.text)) >= 15 "
        "ORDER BY s.slide_num",
        (lid,),
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        total = d["correct_count"] + d["wrong_count"]
        d["accuracy"] = round(d["correct_count"] / total, 2) if total else None
        out.append(d)
    return out


@app.get("/api/dashboard")
def api_dashboard():
    from app.stats import lecture_stats, weak_slides, gaps
    from app.sessiongen import missed_count
    conn = get_conn()
    lect = conn.execute("SELECT COUNT(*) c FROM lectures").fetchone()["c"]
    qs = conn.execute("SELECT COUNT(*) c FROM questions").fetchone()["c"]
    summaries = conn.execute(
        "SELECT COUNT(*) c FROM lectures WHERE summary_status='done'"
    ).fetchone()["c"]
    sessions_today = conn.execute(
        "SELECT COUNT(*) c FROM sessions WHERE created_at >= datetime('now', 'start of day')"
    ).fetchone()["c"]
    conn.close()

    lstats = lecture_stats()
    overall_accuracy = None
    ans_total = sum(l["answers"] for l in lstats)
    ans_correct = sum(l["correct"] for l in lstats)
    if ans_total:
        overall_accuracy = round(ans_correct / ans_total, 2)

    return {
        "lecture_count": lect,
        "question_count": qs,
        "summary_done": summaries,
        "coverage": coverage_stats(),
        "missed_count": missed_count(),
        "sessions_today": sessions_today,
        "overall_accuracy": overall_accuracy,
        "lectures": lstats,
        "weak_slides": weak_slides(),
        "gaps": gaps(),
    }



# ---------- Sessions ----------
@app.post("/api/sessions")
def api_create_session(
    mode: str = Form("practice"),
    target: int = Form(20),
    lecture_id: int = Form(None),
    lecture_ids: list[int] = Form(None),
    time_mode: str = Form("tutor"),
):
    """Create a session. mode: 'practice' (fresh on-the-spot questions from selected
    lectures) or 'review' (previously missed questions). target capped at 59.
    time_mode: 'tutor' (infinite time per question, explanations immediately) or
    'quiz' (total time = 1.5 min/question, explanations only at the end).
    lecture_ids: one or more lectures to draw from (empty/None = all)."""
    from app.sessiongen import create_practice_session, create_review_session
    lids = lecture_ids if lecture_ids else ([lecture_id] if lecture_id else None)
    if mode == "review":
        sid, count = create_review_session(lecture_ids=lids, time_mode=time_mode)
    else:
        sid, count = create_practice_session(lecture_ids=lids, target=target, time_mode=time_mode)
    if count == 0:
        return {"session_id": None, "question_count": 0, "message": "No questions generated"}
    return {"session_id": sid, "question_count": count}


@app.get("/api/missed")
def api_missed():
    """Unresolved missed questions count + breakdown by lecture."""
    from app.sessiongen import missed_count, missed_by_lecture
    return {"count": missed_count(), "by_lecture": missed_by_lecture()}


@app.get("/api/sessions")
def api_list_sessions():
    """All past sessions (newest first), for the Drill 'past sessions' panel."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM sessions ORDER BY updated_at DESC, id DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/sessions/{sid}")
def api_session(sid: int):
    conn = get_conn()
    s = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    if not s:
        conn.close()
        raise HTTPException(404, "Session not found")
    sq = conn.execute(
        "SELECT * FROM session_questions WHERE session_id=? ORDER BY position",
        (sid,),
    ).fetchall()
    conn.close()
    return {"session": dict(s), "questions": [dict(r) for r in sq]}


@app.get("/api/sessions/{sid}/review")
def api_session_review(sid: int):
    """Full question set + correct/wrong status, for end-of-session review."""
    conn = get_conn()
    s = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    if not s:
        conn.close()
        raise HTTPException(404, "Session not found")
    rows = conn.execute(
        "SELECT q.id, q.question, q.options, q.correct_index, q.explanation, sq.answered, "
        "a.selected_index, a.correct AS selected_correct, q.slide_id, "
        "l.title AS lecture_title, s.slide_num, s.text AS slide_text, s.caption AS slide_caption "
        "FROM session_questions sq JOIN questions q ON q.id=sq.question_id "
        "LEFT JOIN lectures l ON l.id=q.lecture_id "
        "LEFT JOIN answers a ON a.question_id=q.id AND a.session_id=? "
        "LEFT JOIN slides s ON s.id=q.slide_id "
        "WHERE sq.session_id=? ORDER BY sq.position",
        (sid, sid),
    ).fetchall()
    conn.close()
    from app.ingest.images import images_for_slides
    out = []
    for r in rows:
        d = dict(r)
        d["options"] = json.loads(d["options"])
        d["source_images"] = images_for_slides([d["slide_id"]]) if d.get("slide_id") else []
        out.append(d)
    return {"session": dict(s), "questions": out}


@app.get("/api/sessions/{sid}/question/{pos}")
def api_session_question(sid: int, pos: int):
    """Return the question at 0-based position `pos` with its current state.
    Tutor mode: already-answered questions reveal the correct answer + explanation.
    Quiz mode (unanswered): correct/explanation are hidden until submit."""
    conn = get_conn()
    s = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    if not s:
        conn.close()
        raise HTTPException(404, "Session not found")
    row = conn.execute(
        "SELECT sq.*, q.question, q.options, q.correct_index, q.explanation, q.slide_id, "
        "l.title AS source_lecture_title, "
        "sl.slide_num AS source_slide_num, sl.text AS source_slide_text, "
        "sl.caption AS source_slide_caption "
        "FROM session_questions sq JOIN questions q ON q.id = sq.question_id "
        "LEFT JOIN lectures l ON l.id = q.lecture_id "
        "LEFT JOIN slides sl ON sl.id = q.slide_id "
        "WHERE sq.session_id=? ORDER BY sq.position LIMIT 1 OFFSET ?",
        (sid, pos),
    ).fetchone()
    conn.close()
    if not row:
        return {"done": True}
    q = dict(row)
    q["options"] = json.loads(q["options"])
    q["id"] = q["question_id"]
    tutor = s["tutor_mode"] != 0
    answered = q["answered"] != 0
    if not (tutor or answered):
        # Quiz, not yet graded: hide the correct answer + explanation
        q["correct_index"] = None
        q["explanation"] = None
    from app.ingest.images import images_for_slides
    q["source_images"] = images_for_slides([q["slide_id"]]) if q.get("slide_id") else []
    q["source_slide"] = {
        "lecture_title": q.get("source_lecture_title") or "",
        "slide_num": q.get("source_slide_num"),
        "text": q.get("source_slide_text") or "",
        "caption": q.get("source_slide_caption") or "",
    }
    return {"done": False, "question": q}


@app.get("/api/sessions/{sid}/next")
def api_next_question(sid: int):
    """First unanswered question (used for resuming sessions)."""
    conn = get_conn()
    s = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    if not s:
        conn.close()
        raise HTTPException(404, "Session not found")
    row = conn.execute(
        "SELECT sq.*, q.question, q.options, q.correct_index, q.explanation, q.level, q.slide_id, "
        "l.title AS source_lecture_title, "
        "s.slide_num AS source_slide_num, s.text AS source_slide_text, "
        "s.caption AS source_slide_caption "
        "FROM session_questions sq JOIN questions q ON q.id = sq.question_id "
        "LEFT JOIN lectures l ON l.id = q.lecture_id "
        "LEFT JOIN slides s ON s.id = q.slide_id "
        "WHERE sq.session_id=? AND sq.answered=0 "
        "ORDER BY sq.position LIMIT 1",
        (sid,),
    ).fetchone()
    conn.close()
    if not row:
        return {"done": True}
    q = dict(row)
    q["options"] = json.loads(q["options"])
    q["id"] = q["question_id"]
    from app.ingest.images import images_for_slides
    slide_ids = [q["slide_id"]] if q.get("slide_id") else []
    q["source_images"] = images_for_slides(slide_ids)
    q["source_slide"] = {
        "lecture_title": q.get("source_lecture_title") or "",
        "slide_num": q.get("source_slide_num"),
        "text": q.get("source_slide_text") or "",
        "caption": q.get("source_slide_caption") or "",
    }
    return {"done": False, "question": q}


class Answer(BaseModel):
    selected_index: int = None  # option index the user chose


class PauseBody(BaseModel):
    elapsed_sec: int = 0


@app.post("/api/sessions/{sid}/pause")
def api_pause(sid: int, body: PauseBody):
    """Persist elapsed time so a paused session resumes with correct remaining time."""
    conn = get_conn()
    s = conn.execute("SELECT id FROM sessions WHERE id=?", (sid,)).fetchone()
    if not s:
        conn.close()
        raise HTTPException(404, "Session not found")
    conn.execute(
        "UPDATE sessions SET elapsed_sec=?, updated_at=datetime('now') WHERE id=?",
        (max(0, body.elapsed_sec), sid),
    )
    conn.commit()
    conn.close()
    return {"ok": True}


@app.post("/api/sessions/{sid}/answer/{question_id}")
def api_answer(sid: int, question_id: int, body: Answer):
    """Record an answer.

    Quiz mode: upsert `session_questions.selected_index` only — the answer can be
    changed until the quiz is submitted for grading. No missed/completed writes.
    Tutor mode: grade immediately (writes answers row, tags missed, increments count)."""
    conn = get_conn()
    s = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    if not s:
        conn.close()
        raise HTTPException(404, "Session not found")
    sq = conn.execute(
        "SELECT sq.question_id, qs.correct_index, qs.explanation, qs.lecture_id "
        "FROM session_questions sq JOIN questions qs ON qs.id = sq.question_id "
        "WHERE sq.session_id=? AND sq.question_id=?",
        (sid, question_id),
    ).fetchone()
    if not sq:
        conn.close()
        raise HTTPException(404, "Not in session")

    tutor = s["tutor_mode"] != 0
    if not tutor:
        # Quiz: provisional selection, changeable until submit
        conn.execute(
            "UPDATE session_questions SET selected_index=? "
            "WHERE session_id=? AND question_id=?",
            (body.selected_index, sid, question_id),
        )
        conn.commit()
        conn.close()
        return {"saved": True}

    # Tutor: grade immediately
    qid = sq["question_id"]
    correct = body.selected_index == sq["correct_index"]
    conn.execute(
        "DELETE FROM answers WHERE question_id=? AND session_id=?",
        (qid, sid),
    )
    conn.execute(
        "INSERT INTO answers(question_id, session_id, correct, selected_index) VALUES(?,?,?,?)",
        (qid, sid, 1 if correct else 0, body.selected_index),
    )
    conn.execute(
        "UPDATE session_questions SET answered=1, selected_index=? "
        "WHERE session_id=? AND question_id=?",
        (body.selected_index, sid, qid),
    )
    conn.execute(
        "UPDATE sessions SET updated_at=datetime('now') WHERE id=?", (sid,)
    )
    _tag_missed(conn, qid, sq["lecture_id"], correct)
    remaining = conn.execute(
        "SELECT COUNT(*) c FROM session_questions WHERE session_id=? AND answered=0",
        (sid,),
    ).fetchone()["c"]
    conn.execute(
        "UPDATE sessions SET completed_count = "
        "(SELECT COUNT(*) FROM session_questions WHERE session_id=? AND answered=1) "
        "WHERE id=?",
        (sid, sid),
    )
    if remaining == 0:
        conn.execute("UPDATE sessions SET status='completed' WHERE id=?", (sid,))
    conn.commit()
    conn.close()
    return {
        "correct": correct,
        "correct_index": sq["correct_index"],
        "explanation": sq["explanation"],
        "remaining": remaining,
    }


def _tag_missed(conn, qid, lecture_id, correct):
    """Tag/resolve a question in the missed table for next-day review."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if not correct:
        conn.execute(
            "INSERT INTO missed(question_id, lecture_id, missed_at, resolved, last_wrong_at) "
            "VALUES(?,?,?,0,?) ON CONFLICT DO NOTHING",
            (qid, lecture_id, now, now),
        )
        conn.execute(
            "UPDATE missed SET resolved=0, last_wrong_at=? WHERE question_id=?",
            (now, qid),
        )
    else:
        conn.execute(
            "UPDATE missed SET resolved=1 WHERE question_id=? AND lecture_id=?",
            (qid, lecture_id),
        )


def _grade_session(conn, sid, unanswered_as_wrong=True):
    """Grade every question in a session from its final selection.
    Returns (graded_incorrect, total)."""
    s = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    if not s:
        raise ValueError("Session not found")
    rows = conn.execute(
        "SELECT sq.question_id, sq.selected_index, qs.correct_index, qs.lecture_id "
        "FROM session_questions sq JOIN questions qs ON qs.id=sq.question_id "
        "WHERE sq.session_id=? ORDER BY sq.position",
        (sid,),
    ).fetchall()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    graded_incorrect = 0
    for r in rows:
        qid = r["question_id"]
        sel = r["selected_index"]
        correct = sel is not None and sel == r["correct_index"]
        if not correct:
            graded_incorrect += 1
        conn.execute(
            "DELETE FROM answers WHERE question_id=? AND session_id=?",
            (qid, sid),
        )
        conn.execute(
            "INSERT INTO answers(question_id, session_id, correct, selected_index) "
            "VALUES(?,?,?,?)",
            (qid, sid, 1 if correct else 0, sel),
        )
        conn.execute(
            "UPDATE session_questions SET answered=1 WHERE session_id=? AND question_id=?",
            (sid, qid),
        )
        _tag_missed(conn, qid, r["lecture_id"], correct)
    total = len(rows)
    conn.execute(
        "UPDATE sessions SET completed_count=?, status='completed', "
        "updated_at=datetime('now') WHERE id=?",
        (total, sid),
    )
    return graded_incorrect, total


@app.post("/api/sessions/{sid}/submit")
def api_submit(sid: int):
    """Grade a quiz from its final selections. Earlier picks are not penalized —
    only the current `selected_index` per question counts."""
    conn = get_conn()
    s = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    if not s:
        conn.close()
        raise HTTPException(404, "Session not found")
    graded_incorrect, total = _grade_session(conn, sid)
    conn.commit()
    conn.close()
    return {"submitted": True, "graded_incorrect": graded_incorrect, "total": total}


@app.post("/api/sessions/{sid}/timeout")
def api_timeout(sid: int):
    """Quiz-mode timer expired: grade every question from its final selection,
    treating any question with no selection as incorrect."""
    conn = get_conn()
    s = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    if not s:
        conn.close()
        raise HTTPException(404, "Session not found")
    graded_incorrect, total = _grade_session(conn, sid)
    conn.commit()
    conn.close()
    return {"timeout": True, "graded_incorrect": graded_incorrect, "total": total}


@app.delete("/api/sessions/{sid}")
def api_delete_session(sid: int):
    """Delete a past session and its answer history."""
    conn = get_conn()
    row = conn.execute("SELECT id FROM sessions WHERE id=?", (sid,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Session not found")
    conn.execute("DELETE FROM answers WHERE session_id=?", (sid,))
    conn.execute("DELETE FROM sessions WHERE id=?", (sid,))
    conn.commit()
    conn.close()
    return {"deleted": sid}
