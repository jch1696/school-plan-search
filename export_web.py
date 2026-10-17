"""폰·웹용 목록(docs/data.json) 관리.

docs/ 폴더가 GitHub Pages로 공개되어 폰에서 제목을 검색하고 정보공개포털 원문으로 바로 간다.
목록은 두 군데서 늘어난다:
  - PC:  python export_web.py                 DB와 data.json을 합쳐 다시 내보낸다
         (폰에서 모은 문서는 DB에 들어가고, DB 전체가 data.json으로 나간다)
  - 폰:  GitHub Actions(collect.yml)가 python export_web.py collect "검색어" 365
         포털을 검색해 data.json에만 추가한다 (DB 없음, fastapi 없음 → opengo만 쓴다)
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

import opengo

OUT = Path(__file__).with_name("docs") / "data.json"
FIELDS = ["reg_no", "date", "title", "school", "org", "open_type", "produced", "instt_se", "files", "level", "region"]


def load() -> dict:
    if not OUT.exists():
        return {"rows": [], "keywords": []}
    d = json.loads(OUT.read_text(encoding="utf-8"))
    return {"rows": [dict(zip(d["fields"], a)) for a in d["rows"]], "keywords": d.get("keywords", [])}


def save(rows: list[dict], keywords: list[str]) -> int:
    rows = sorted(rows, key=lambda r: (r.get("date") or "", r.get("produced") or ""), reverse=True)
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps({"exported": time.strftime("%Y-%m-%d %H:%M"), "count": len(rows), "keywords": keywords[:30],
                               "fields": FIELDS, "rows": [[r.get(f) for f in FIELDS] for r in rows]},
                              ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return len(rows)


def to_web(r: dict) -> dict:
    """DB 행 또는 포털 검색 결과 행 → data.json 행."""
    files = r["files"] if isinstance(r["files"], str) else "|".join(r["files"] or [])
    return {"reg_no": r["reg_no"], "date": r["date"], "title": r["title"], "school": r["school"], "org": r["org"],
            "open_type": r["open_type"], "produced": r["produced"], "instt_se": r["instt_se"], "files": files,
            "level": opengo.level_of(r["school"]), "region": opengo.region_of(r["org"])}


def export_from_db() -> tuple[int, int]:
    """(PC) 폰에서 모은 문서를 DB에 넣고, DB 전체를 내보낸다. (전체 건수, DB에 새로 들어간 건수) 반환."""
    con = opengo.init_db()
    con.row_factory = sqlite3.Row
    web = load()
    have = {r[0] for r in con.execute("SELECT reg_no FROM docs")}
    new = [r for r in web["rows"] if r["reg_no"] not in have]
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    for r in new:  # unit_job, theme, doc_no, keyword는 data.json에 없어서 비워 둔다
        con.execute("INSERT OR IGNORE INTO docs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (r["reg_no"], r["title"], r["org"], r["school"], None, None, r["produced"], r["date"],
                     r["open_type"], r["instt_se"], r["files"], None, None, now))
    con.commit()
    rows = [to_web(dict(r)) for r in con.execute("SELECT * FROM docs")]
    keywords = [k for (k,) in con.execute("SELECT keyword FROM docs WHERE keyword IS NOT NULL "
                                          "GROUP BY keyword ORDER BY MAX(fetched_at) DESC")]
    return save(rows, keywords), len(new)


def collect_to_web(keyword: str, days: int) -> tuple[dict, int]:
    """(GitHub Actions) 포털을 검색해 data.json에 추가한다. ({검색어: 건수}, 새 문서 수) 반환."""
    web = load()
    rows = {r["reg_no"]: r for r in web["rows"]}
    before, found = len(rows), {}
    for kw in [keyword] + [v for v in [opengo.expand_keyword(keyword)] if v]:
        items = opengo.search_all(kw, days=days, max_pages=20)
        found[kw] = len(items)
        for r in items:
            rows.setdefault(r["reg_no"], to_web(r))
    keywords = [keyword] + [k for k in web["keywords"] if k != keyword]
    save(list(rows.values()), keywords)
    return found, len(rows) - before


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "collect":
        kw, days = sys.argv[2].strip(), int(sys.argv[3]) if len(sys.argv) > 3 else 365
        found, added = collect_to_web(kw, days)
        print(", ".join(f"'{k}' {n}건" for k, n in found.items()) + f" 확인, 새 문서 {added}건 추가")
    else:
        total, imported = export_from_db()
        print(f"웹에서 모은 문서 {imported}건을 DB에 넣음. {total}건 -> {OUT} ({OUT.stat().st_size // 1024} KB)")
