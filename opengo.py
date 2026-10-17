"""정보공개포털(open.go.kr) 원문정보 검색 모듈.

사이트가 브라우저에서 보내는 것과 같은 요청을 보내 결과를 받아온다.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import requests

try:  # 보안 프로그램이 HTTPS를 검사하는 PC에서도 되도록 Windows 인증서 저장소 사용
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

BASE = "https://www.open.go.kr"
LIST_PAGE = f"{BASE}/othicInfo/infoList/orginlInfoList.do"
LIST_AJAX = f"{BASE}/othicInfo/infoList/orginlInfoList.ajax"
DETAIL_PAGE = f"{BASE}/othicInfo/infoList/infoListDetl.do"

# 기관 유형 코드: C 중앙행정기관, W 광역자치단체, B 기초자치단체, E 교육청, P 공공기관
INSTT_EDU = "E"

DB_PATH = Path(__file__).with_name("opengo.db")


UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
AJAX_HEADERS = {
    "X-Requested-With": "XMLHttpRequest",
    "Referer": LIST_PAGE,
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
}


def _session() -> requests.Session:
    """사이트가 브라우저에서 하는 초기화(페이지 접속 + 지문 등록)를 흉내내 세션을 만든다.

    지문 등록을 건너뛰면 검색 요청이 code 491로 거부된다.
    """
    import hashlib
    s = requests.Session()
    s.headers.update({"User-Agent": UA})
    s.get(LIST_PAGE, timeout=30)  # 세션 쿠키 확보
    fp = hashlib.sha256(json.dumps({"userAgent": UA, "platform": "Win32"}).encode()).hexdigest().upper()
    s.post(f"{BASE}/common/util/setFingerPrint.ajax", data={"fingerprint": fp},
           headers=AJAX_HEADERS, timeout=30)
    return s


def search(keyword: str, *, days: int = 30, start: str | None = None, end: str | None = None,
           instt_se: str = INSTT_EDU, include_schools: bool = True, page: int = 1,
           rows: int = 50, session: requests.Session | None = None) -> tuple[int, list[dict]]:
    """한 페이지 검색. (전체 건수, 결과 목록) 반환."""
    s = session or _session()
    if end is None:
        end = date.today().strftime("%Y%m%d")
    if start is None:
        start = (date.today() - timedelta(days=days)).strftime("%Y%m%d")
    payload = {
        "kwd": keyword, "preKwds": keyword, "reSrchFlag": "off",
        "insttSeCd": instt_se, "eduYn": "Y" if include_schools else "N",
        "startDate": start, "endDate": end,
        "insttCd": "", "insttCdNm": "", "searchMainYn": "N",
        "viewPage": str(page), "rowPage": str(rows), "sort": "s",
        "pSelt": "", "othbcSeCd": "",
    }
    r = s.post(LIST_AJAX, data=payload, headers=AJAX_HEADERS, timeout=30)
    r.raise_for_status()
    res = r.json()["result"]
    if str(res.get("code")) != "200":
        raise RuntimeError(f"검색 실패 (code {res.get('code')})")
    return int(res.get("rtnTotal") or 0), _normalize(res.get("rtnList") or [])


def search_all(keyword: str, *, max_pages: int = 20, **kw) -> list[dict]:
    """모든 페이지를 돌며 결과를 모은다."""
    s = _session()
    rows = kw.pop("rows", 50)
    out: list[dict] = []
    total, items = search(keyword, rows=rows, page=1, session=s, **kw)
    out += items
    pages = min(max_pages, -(-total // rows))
    for p in range(2, pages + 1):
        time.sleep(0.5)
        _, items = search(keyword, rows=rows, page=p, session=s, **kw)
        out += items
    return out


def _normalize(items: list[dict]) -> list[dict]:
    rows = []
    for d in items:
        files = [f for f in (d.get("FILE_NM") or "").split("|") if f]
        rows.append({
            "reg_no": d.get("PRDCTN_INSTT_REGIST_NO"),
            "title": d.get("INFO_SJ"),
            "org": d.get("NFLST_CHRG_DEPT_NM"),      # 교육청 지원청 학교
            "school": d.get("CHRG_DEPT_NM"),
            "unit_job": d.get("UNIT_JOB_NM"),
            "theme": d.get("RQEST_TY_THEMA_NM"),
            "produced": d.get("PRDCTN_DT"),         # yyyymmddHHMMSS
            "date": d.get("P_DATE"),                 # yyyymmdd
            "open_type": {"1": "공개", "2": "부분공개"}.get(d.get("OTHBC_SE_CD"), d.get("OTHBC_SE_CD")),
            "instt_se": d.get("INSTT_SE_CD"),
            "files": files,
            "doc_no": d.get("DOC_NO"),
        })
    return rows


def detail_form(row: dict) -> dict:
    """원문 상세 페이지로 가기 위한 POST 폼 값."""
    return {"prdnNstRgstNo": row["reg_no"], "prdnDt": row["produced"],
            "nstSeCd": row["instt_se"], "title": "원문정보"}


# ---------- 저장 ----------

def init_db(path: Path = DB_PATH) -> sqlite3.Connection:
    con = sqlite3.connect(path, check_same_thread=False)
    con.execute("""CREATE TABLE IF NOT EXISTS docs (
        reg_no TEXT PRIMARY KEY, title TEXT, org TEXT, school TEXT, unit_job TEXT,
        theme TEXT, produced TEXT, date TEXT, open_type TEXT, instt_se TEXT,
        files TEXT, doc_no TEXT, keyword TEXT, fetched_at TEXT)""")
    con.execute("CREATE INDEX IF NOT EXISTS idx_docs_date ON docs(date)")
    return con


def save(rows: list[dict], keyword: str, con: sqlite3.Connection) -> int:
    n = 0
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    for r in rows:
        cur = con.execute("""INSERT OR IGNORE INTO docs VALUES
            (:reg_no,:title,:org,:school,:unit_job,:theme,:produced,:date,:open_type,
             :instt_se,:files,:doc_no,:keyword,:fetched_at)""",
            {**r, "files": "|".join(r["files"]), "keyword": keyword, "fetched_at": now})
        n += cur.rowcount
    con.commit()
    return n


if __name__ == "__main__":
    kw = sys.argv[1] if len(sys.argv) > 1 else "학예회 계획"
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    rows = search_all(kw, days=days)
    con = init_db()
    added = save(rows, kw, con)
    print(f"'{kw}' 최근 {days}일: {len(rows)}건 수집, 신규 {added}건 저장 -> {DB_PATH}")
    for r in rows[:10]:
        print(f"- [{r['date']}] {r['title']}  ({r['school']})  파일 {len(r['files'])}개")
