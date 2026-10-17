"""폰·웹용 목록 내보내기.

opengo.db의 docs 테이블을 docs/data.json으로 쓴다. 이 폴더가 GitHub Pages로 공개되어
폰에서 제목을 검색하고 정보공개포털 원문으로 바로 갈 수 있다. (다운로드·본문검색은 PC 전용)

실행:  python export_web.py
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import opengo
from server import level_of, region_of

OUT = Path(__file__).with_name("docs") / "data.json"
FIELDS = ["reg_no", "date", "title", "school", "org", "open_type", "produced", "instt_se", "nfiles", "level", "region"]


def export() -> int:
    con = opengo.init_db()
    rows = []
    for r in con.execute("SELECT reg_no, date, title, school, org, open_type, produced, instt_se, files "
                         "FROM docs ORDER BY date DESC, produced DESC"):
        reg_no, date, title, school, org, open_type, produced, instt_se, files = r
        nfiles = len([f for f in (files or "").split("|") if f])
        rows.append([reg_no, date, title, school, org, open_type, produced, instt_se, nfiles,
                     level_of(school), region_of(org)])
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps({"exported": time.strftime("%Y-%m-%d %H:%M"), "fields": FIELDS, "rows": rows},
                              ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return len(rows)


if __name__ == "__main__":
    n = export()
    print(f"{n}건 -> {OUT} ({OUT.stat().st_size // 1024} KB)")
