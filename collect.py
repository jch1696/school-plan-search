"""관심 검색어를 한꺼번에 수집한다. 주기적으로 실행하면 새 문서만 쌓인다.

실행:  python collect.py            (keywords.txt의 검색어, 최근 30일)
       python collect.py 90         (최근 90일)
"""
import sys
from pathlib import Path

import opengo

KW_FILE = Path(__file__).with_name("keywords.txt")

days = int(sys.argv[1]) if len(sys.argv) > 1 else 30
keywords = [l.strip() for l in KW_FILE.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]
con = opengo.init_db()
for kw in keywords:
    try:
        rows = opengo.search_all(kw, days=days, max_pages=20)
        added = opengo.save(rows, kw, con)
        print(f"{kw}: {len(rows)}건 중 신규 {added}건")
    except Exception as e:  # noqa: BLE001
        print(f"{kw}: 실패 ({e})")
