"""학교 계획서 검색기 - 로컬 서버.

실행:  python server.py   (브라우저에서 http://localhost:8765)
화면은 static/index.html, 데이터 처리는 opengo.py / wonmun.py.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
import uuid
import webbrowser
from datetime import date, timedelta
from pathlib import Path

import uvicorn
from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import opengo
import wonmun

HERE = Path(__file__).parent
PORT = 8765
app = FastAPI(title="학교 계획서 검색기")

JOBS: dict[str, dict] = {}  # 오래 걸리는 작업(수집, 다운로드)의 진행 상황


def db() -> sqlite3.Connection:
    con = opengo.init_db()
    con.row_factory = sqlite3.Row
    wonmun.init_fts(con)
    return con


level_of, region_of, expand_keyword = opengo.level_of, opengo.region_of, opengo.expand_keyword


def run_job(job_id: str, fn):
    def _w():
        try:
            fn(JOBS[job_id])
            JOBS[job_id]["state"] = "done"
        except Exception as e:  # noqa: BLE001
            JOBS[job_id].update(state="error", message=str(e)[:300])
    threading.Thread(target=_w, daemon=True).start()


# ---------------- 문서 ----------------

@app.get("/api/docs")
def list_docs(q: str = "", school: str = "", level: str = "", region: str = "", open_type: str = "",
              days: int = 90, only_got: bool = False, offset: int = 0, limit: int = 100):
    """조건에 맞는 전체 건수(total)와 offset부터 limit건(rows)을 돌려준다."""
    con = db()
    sql = """SELECT d.*, (SELECT COUNT(*) FROM files f WHERE f.reg_no=d.reg_no AND f.status='ok') AS got
             FROM docs d WHERE d.date >= ?"""
    params: list = [(date.today() - timedelta(days=days)).strftime("%Y%m%d")]
    for w in q.split():
        sql += " AND d.title LIKE ?"
        params.append(f"%{w}%")
    if school.strip():
        sql += " AND d.org LIKE ?"
        params.append(f"%{school.strip()}%")
    if open_type:  # "공개" / "부분공개"
        sql += " AND d.open_type = ?"
        params.append(open_type)
    sql += " ORDER BY d.date DESC, d.produced DESC"
    rows, total = [], 0
    for r in con.execute(sql, params):
        lv, rg = level_of(r["school"]), region_of(r["org"])
        if level and lv not in level.split(","):
            continue
        if region and rg != region:
            continue
        if only_got and not r["got"]:
            continue
        total += 1
        if total <= offset or len(rows) >= limit:
            continue
        rows.append({"reg_no": r["reg_no"], "date": r["date"], "title": r["title"], "school": r["school"],
                     "org": r["org"], "unit_job": r["unit_job"], "open_type": r["open_type"],
                     "files": [f for f in (r["files"] or "").split("|") if f], "produced": r["produced"],
                     "instt_se": r["instt_se"], "got": r["got"], "level": lv, "region": rg})
    return {"total": total, "offset": offset, "rows": rows}


@app.get("/api/meta")
def meta():
    con = db()
    regions = sorted({region_of(r[0]) for r in con.execute("SELECT DISTINCT org FROM docs")})
    stale, other = wonmun.stale_files(con)
    return {
        "stale": len(stale), "other_pc": other, "need_ocr": len(wonmun.ocr_candidates(con)),
        "docs": con.execute("SELECT COUNT(*) FROM docs").fetchone()[0],
        "files": con.execute("SELECT COUNT(*) FROM files WHERE status='ok'").fetchone()[0],
        "keywords": [r[0] for r in con.execute("SELECT keyword FROM docs WHERE keyword IS NOT NULL "
                                               "GROUP BY keyword ORDER BY MAX(fetched_at) DESC")],
        "regions": regions,
        "download_dir": str(wonmun.download_dir()),
        "prefix": wonmun.load_settings().get("prefix", True),
    }


@app.get("/api/docs/{reg_no}/files")
def doc_files(reg_no: str):
    con = db()
    d = con.execute("SELECT * FROM docs WHERE reg_no=?", (reg_no,)).fetchone()
    if not d:
        raise HTTPException(404)
    try:
        vo = wonmun.fetch_detail(opengo._session(), d["reg_no"], d["produced"], d["instt_se"])
        files = vo.get("fileList") or []
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"정보공개포털에서 파일 목록을 가져오지 못했습니다: {e}")
    have = {r["file_id"]: dict(r) for r in con.execute("SELECT * FROM files WHERE reg_no=?", (reg_no,))}
    out = []
    for f in files:
        h = have.get(f["fileId"], {})
        exists = bool(h.get("path")) and Path(h["path"]).exists()
        out.append({"file_id": f["fileId"], "name": f["fileNm"], "kind": f.get("fileSeDc", ""),
                    "size": int(f.get("fileByteNum") or 0), "closed": f.get("fileOppYn") == "N",
                    "status": "ok" if (h.get("status") == "ok" and exists) else h.get("status"),
                    "path": h.get("path") if exists else None, "error": h.get("error")})
    return {"doc": dict(d), "detail_form": opengo.detail_form(dict(d)), "files": out}


@app.get("/api/text/{file_id}")
def file_text(file_id: str):
    con = db()
    r = con.execute("SELECT text FROM fts WHERE file_id=?", (file_id,)).fetchone()
    return {"text": (r[0] if r else "") or ""}


@app.get("/api/search_text")
def search_text(q: str, limit: int = 200):
    con = db()
    try:
        hits = wonmun.search_text(con, q, limit=limit)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"검색어를 확인해 주세요: {e}")
    out = []
    for reg_no, fid, snip in hits:
        d = con.execute("SELECT title, school, date, org FROM docs WHERE reg_no=?", (reg_no,)).fetchone()
        f = con.execute("SELECT file_nm, path FROM files WHERE file_id=?", (fid,)).fetchone()
        if d and f:
            out.append({"reg_no": reg_no, "file_id": fid, "title": d[0], "school": d[1], "date": d[2],
                        "org": d[3], "file": f[0], "path": f[1], "snippet": " ".join(snip.split())})
    return {"total": len(out), "rows": out}


# ---------------- 오래 걸리는 작업 ----------------

@app.post("/api/collect")
def collect(body: dict = Body(...)):
    keyword, days = (body.get("keyword") or "").strip(), int(body.get("days") or 30)
    max_pages = int(body.get("max_pages") or 10)
    if not keyword:
        raise HTTPException(400, "검색어를 입력하세요")
    job_id = uuid.uuid4().hex
    JOBS[job_id] = {"state": "running", "message": "정보공개포털에 검색 중...", "progress": 0}

    variants = [keyword] + [v for v in [expand_keyword(keyword)] if v]

    def work(job):
        s = opengo._session()
        con, found, added = db(), {}, 0
        for vi, kw in enumerate(variants):
            share = 1 / len(variants)
            total, first = opengo.search(kw, days=days, rows=50, page=1, session=s)
            rows = list(first)
            pages = min(max_pages, max(1, -(-total // 50)))
            job.update(message=f"'{kw}' {total}건 중 {len(rows)}건 받음", progress=(vi + 1 / pages) * share)
            for p in range(2, pages + 1):
                time.sleep(0.4)
                _, items = opengo.search(kw, days=days, rows=50, page=p, session=s)
                rows += items
                job.update(message=f"'{kw}' {total}건 중 {len(rows)}건 받음", progress=(vi + p / pages) * share)
            found[kw] = len(rows)
            added += opengo.save(rows, kw, con)
        detail = ", ".join(f"'{k}' {n}건" for k, n in found.items())
        job.update(message=f"{detail} 확인, 새 문서 {added}건 저장", added=added, progress=1)
    run_job(job_id, work)
    return {"job": job_id}


@app.post("/api/download")
def download(body: dict = Body(...)):
    """{reg_no, file_ids:[...]} 또는 {reg_nos:[...], attachments:bool} (한꺼번에 받기)."""
    job_id = uuid.uuid4().hex
    JOBS[job_id] = {"state": "running", "message": "내려받는 중...", "progress": 0, "results": []}
    prefix = wonmun.load_settings().get("prefix", True)

    def work(job):
        con = db()
        s = opengo._session()
        if body.get("reg_no"):
            targets = [(body["reg_no"], set(body.get("file_ids") or []), True)]
        else:
            targets = [(r, None, False) for r in body.get("reg_nos") or []]
        for i, (reg_no, fids, force) in enumerate(targets, 1):
            d = con.execute("SELECT * FROM docs WHERE reg_no=?", (reg_no,)).fetchone()
            if not d:
                continue
            job["message"] = f"{i}/{len(targets)} {d['title']}"
            res = wonmun.download_doc(con, dict(d), attachments=bool(body.get("attachments")), session=s,
                                      file_ids=fids, with_prefix=prefix, force=force)
            for r in res:
                r["title"] = d["title"]
            job["results"] += res
            job["progress"] = i / len(targets)
        ok = sum(r["status"] == "완료" for r in job["results"])
        fail = sum(r["status"] == "실패" for r in job["results"])
        job["message"] = f"완료 {ok}개, 실패 {fail}개"
    run_job(job_id, work)
    return {"job": job_id}


@app.post("/api/ocr")
def ocr(body: dict = Body(default={})):
    """{file_ids:[...]} 그 파일만, 없으면 글자 없는 PDF 전부를 OCR한다."""
    ids = body.get("file_ids") or wonmun.ocr_candidates(db())
    if not ids:
        raise HTTPException(400, "OCR할 파일이 없습니다")
    job_id = uuid.uuid4().hex
    JOBS[job_id] = {"state": "running", "message": "OCR 준비 중...", "progress": 0, "results": []}

    def work(job):
        con = db()
        for i, fid in enumerate(ids):
            name = (con.execute("SELECT file_nm FROM files WHERE file_id=?", (fid,)).fetchone() or ["?"])[0]

            def tick(p, n, i=i):
                job.update(message=f"{i + 1}/{len(ids)} {name} ({p}/{n}쪽)", progress=(i + p / n) / len(ids))
            try:
                job["results"].append({"file": name, "status": "완료", "chars": wonmun.ocr_file(con, fid, tick)})
            except Exception as e:  # noqa: BLE001
                job["results"].append({"file": name, "status": "실패", "error": str(e)[:200]})
        ok = sum(r["status"] == "완료" for r in job["results"])
        job.update(message=f"OCR 완료 {ok}개, 실패 {len(ids) - ok}개", progress=1)
    run_job(job_id, work)
    return {"job": job_id}


@app.post("/api/files/delete")
def delete_files(body: dict = Body(...)):
    """받은 파일을 휴지통으로 보내고 기록도 지운다."""
    return {"results": wonmun.delete_files(db(), body.get("file_ids") or [])}


@app.get("/api/files/stale")
def stale_files():
    stale, other = wonmun.stale_files(db())
    return {"stale": len(stale), "other_pc": other}


@app.post("/api/files/cleanup")
def cleanup_files():
    """탐색기에서 지운 파일의 기록을 정리한다."""
    return {"removed": wonmun.cleanup_files(db())}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    if job_id not in JOBS:
        raise HTTPException(404)
    return JOBS[job_id]


# ---------------- 설정 ----------------

@app.post("/api/settings")
def set_settings(body: dict = Body(...)):
    st = wonmun.load_settings()
    if "download_dir" in body:
        p = Path(body["download_dir"]).expanduser()
        p.mkdir(parents=True, exist_ok=True)
        st["download_dir"] = str(p)
    if "prefix" in body:
        st["prefix"] = bool(body["prefix"])
    wonmun.save_settings(st)
    return meta()


@app.post("/api/pick_folder")
def pick_folder():
    """Windows 폴더 선택 창을 띄운다 (서버가 같은 PC에서 돌기 때문에 가능)."""
    result: dict = {}

    def _dlg():
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            result["path"] = filedialog.askdirectory(initialdir=str(wonmun.download_dir()), title="원문을 저장할 폴더 선택")
            root.destroy()
        except Exception as e:  # noqa: BLE001
            result["error"] = str(e)
    t = threading.Thread(target=_dlg)
    t.start()
    t.join()
    if result.get("error"):
        raise HTTPException(500, result["error"])
    if result.get("path"):
        set_settings({"download_dir": result["path"]})
    return meta()


@app.post("/api/open_folder")
def open_folder(body: dict = Body(default={})):
    target = body.get("path") or str(wonmun.download_dir())
    p = Path(target)
    if p.is_file():
        os.startfile(str(p))  # 파일이면 연결 프로그램으로 연다
    else:
        os.startfile(str(p if p.exists() else wonmun.download_dir()))
    return {"ok": True}


# ---------------- 화면 ----------------

@app.get("/")
def index():
    return FileResponse(HERE / "static" / "index.html")


app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


if __name__ == "__main__":
    threading.Timer(1.5, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
