"""원문 파일 다운로드 + 본문 텍스트 추출.

정보공개포털이 브라우저에서 하는 절차(요청 → 개인정보 필터 → 다운로드)를 그대로 따른다.
본문 PDF("결재문서본문.pdf")는 대부분 받을 수 있고, 첨부 HWP는 사이트가 제공하지 않는 경우가 많다.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path

import requests

import opengo

FILES_DIR = Path(__file__).with_name("files")
WONMUN = f"{opengo.BASE}/util/wonmunUtils"


class WonmunError(RuntimeError):
    pass


def _ok(res: dict) -> bool:
    return str(res.get("error_code")) in ("0", "00")


def fetch_detail(s: requests.Session, reg_no: str, produced: str, instt_se: str) -> dict:
    """상세 페이지에 심어진 문서 정보(JSON)를 꺼낸다."""
    r = s.post(opengo.DETAIL_PAGE, data={"prdnNstRgstNo": reg_no, "prdnDt": produced,
                                         "nstSeCd": instt_se, "title": "원문정보"}, timeout=30)
    r.raise_for_status()
    m = re.search(r"var\s+result\s*=\s*(\{.*?\});\s*\n", r.text, re.S)
    if not m:
        raise WonmunError("상세 페이지에서 문서 정보를 찾지 못했습니다")
    return json.loads(m.group(1))["openCateSearchVO"]


SETTINGS_PATH = Path(__file__).with_name("settings.json")


def load_settings() -> dict:
    try:
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def save_settings(d: dict) -> None:
    SETTINGS_PATH.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


def download_dir() -> Path:
    """사용자가 정한 저장 폴더. 없으면 기본 files/ 폴더."""
    return Path(load_settings().get("download_dir") or FILES_DIR)


def safe_name(name: str, limit: int = 120) -> str:
    name = re.sub(r'[\\/:*?"<>|\r\n]', "_", name).strip(" .")
    return name[:limit] if len(name) > limit else name


def download_file(s: requests.Session, vo: dict, f: dict, out_dir: Path, save_as: str | None = None) -> Path:
    """파일 하나를 내려받아 저장 경로를 돌려준다. 실패하면 WonmunError.

    save_as를 주면 그 이름으로(같은 이름이 있으면 (2), (3)… 붙여서) 저장한다.
    """
    file_id, file_nm = f["fileId"], f["fileNm"]
    common = {"docId": vo["docNo"], "orgCd": vo["nstCd"], "prdnNstRgstNo": vo["prdnNstRgstNo"],
              "oppSeCd": vo["oppSeCd"], "isPdf": "N"}
    # 1) 요청
    r1 = s.post(f"{WONMUN}/wonmunFileRequest.ajax", headers=opengo.AJAX_HEADERS, timeout=120,
                data={**common, "fileId": file_id, "esbFileName": file_nm, "ctDate": vo["prdnDt"],
                      "chrgDeptNm": vo["chrgDeptNm"]}).json()["result"]
    if not _ok(r1):
        raise WonmunError((r1.get("error_msg") or r1.get("rtnMessage") or "요청 실패").strip())
    # 2) 사이트 JS는 요청 결과를 바로 필터 단계로 넘긴다 (error_code 01이면 이미 준비된 파일)
    r2 = r1
    if str(r2.get("error_code")) != "01":
        # 3) 개인정보 필터
        filter_data = {
            "prdnNstRgstNo": vo["prdnNstRgstNo"], "prdnDt": vo["prdnDt"],
            "esbFilePath": r2["esbFilePath"], "esbFileName": r2["esbFileName"], "fileName": r2["fileName"],
            "fileId": file_id, "orglPrdnNstCd": r2.get("orglPrdnNstCd", ""), "nstCd": vo["nstCd"],
            "orgCd": vo["nstCd"], "orgSeCd": vo["nstSeCd"], "infoSj": vo["infoSj"],
            "chgrNmpn": vo.get("chgrNmpn", ""), "orgNm": vo.get("prcsNstNm", ""),
            "chrgDeptCd": vo.get("chrgDeptCd", ""), "chrgDeptNm": vo.get("chrgDeptNm", ""),
            "nstClNm": vo.get("nstClNm", ""), "prsrvPdCd": vo.get("prsrvPdCd", ""), "docId": vo["docNo"],
            "isPdf": r2["isPdf"], "step": "step2", "closegvrnYn": r2.get("closegvrnYn", ""),
            "ndnfFiltrRndabtYn": vo.get("ndnfFiltrRndabtYn", ""), "rceptInsttCd": vo.get("rceptInsttCd", ""),
            "rceptInsttCdNm": vo.get("rceptInsttCdNm", ""), "mngrTelno": r2.get("mngrTelno", ""),
        }
        for attempt in range(4):  # "(05) 처리량이 많아 지연" 은 잠시 뒤 다시 시도하면 대개 된다
            r3 = s.post(f"{WONMUN}/wonmunFileFilter.ajax", headers=opengo.AJAX_HEADERS, timeout=300,
                        data=filter_data).json()["result"]
            if _ok(r3) or "(05)" not in str(r3.get("error_msg", "")) or attempt == 3:
                break
            time.sleep(8)
        if not _ok(r3):
            raise WonmunError((r3.get("error_msg") or "필터 실패").strip())
        final = r3
    else:
        final = r2  # 이미 서버에 준비된 파일
    # 4) 다운로드
    r4 = s.post(f"{WONMUN}/wonmunFileDownload.down", timeout=300, stream=True, data={
        "esbFilePath": final["esbFilePath"], "esbFileName": final["esbFileName"], "fileName": final["fileName"],
        "isPdf": final["isPdf"], "prdnNstRgstNo": vo["prdnNstRgstNo"], "prdnDt": vo["prdnDt"],
        "fileId": file_id, "gubun": "esbFilePath"})
    r4.raise_for_status()
    ctype = r4.headers.get("Content-Type", "")
    if "text/html" in ctype:
        raise WonmunError("다운로드 응답이 파일이 아닙니다")
    out_dir.mkdir(parents=True, exist_ok=True)
    if save_as:
        path = out_dir / safe_name(save_as)
        stem, suf, k = path.stem, path.suffix, 2
        while path.exists():
            path = out_dir / f"{stem} ({k}){suf}"
            k += 1
    else:
        path = out_dir / f"{file_id}_{safe_name(file_nm)}"
    with open(path, "wb") as fh:
        for chunk in r4.iter_content(65536):
            fh.write(chunk)
    return path


# ---------- 텍스트 추출 ----------

def extract_text(path: Path) -> str:
    ext = path.suffix.lower()
    try:
        if ext == ".pdf":
            from pypdf import PdfReader
            return "\n".join((p.extract_text() or "") for p in PdfReader(str(path)).pages)
        if ext == ".hwpx":
            import zipfile
            with zipfile.ZipFile(path) as z:
                xml = "".join(z.read(n).decode("utf-8", "ignore") for n in z.namelist()
                              if n.startswith("Contents/section"))
            return re.sub(r"<[^>]+>", " ", xml)
        if ext == ".hwp":
            return _hwp_text(path)
    except Exception as e:  # noqa: BLE001
        return f"[텍스트 추출 실패: {e}]"
    return ""


OCR_MARK = "[OCR] "  # OCR로 읽은 글자 앞에 붙여 화면에서 구분한다
OCR_MAX_PAGES = 30


def needs_ocr(path: Path, text: str) -> bool:
    """글자가 거의 없는 PDF = 스캔본으로 본다."""
    return path.suffix.lower() == ".pdf" and len(text.strip()) < 20


def ocr_pdf(path: Path, on_page=None) -> str:
    """스캔 PDF를 Windows 내장 OCR(한국어)로 읽는다. PyMuPDF로 페이지를 그림으로 바꿔 넘긴다."""
    import asyncio

    import fitz
    from winrt.windows.globalization import Language
    from winrt.windows.graphics.imaging import BitmapDecoder
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream

    engine = OcrEngine.try_create_from_language(Language("ko"))
    if engine is None:
        raise RuntimeError("Windows 한국어 OCR이 없습니다 (설정 > 언어에서 한국어 추가)")

    async def page_text(png: bytes) -> str:
        stream = InMemoryRandomAccessStream()
        writer = DataWriter(stream)
        writer.write_bytes(png)
        await writer.store_async()
        writer.detach_stream()
        stream.seek(0)
        bmp = await (await BitmapDecoder.create_async(stream)).get_software_bitmap_async()
        res = await engine.recognize_async(bmp)
        return "\n".join(line.text for line in res.lines)

    async def run() -> list[str]:
        out = []
        with fitz.open(str(path)) as doc:
            n = min(len(doc), OCR_MAX_PAGES)
            for i in range(n):
                out.append(await page_text(doc[i].get_pixmap(dpi=200).tobytes("png")))
                if on_page:
                    on_page(i + 1, n)
        return out

    return OCR_MARK + "\n\n".join(asyncio.run(run()))


def ocr_candidates(con: sqlite3.Connection) -> list[str]:
    """받아둔 PDF 중 글자가 없어 OCR이 필요한 file_id 목록."""
    rows = con.execute("""SELECT f.file_id, f.path, coalesce(t.text,'') FROM files f
                          LEFT JOIN fts t ON t.file_id=f.file_id WHERE f.status='ok'""").fetchall()
    return [fid for fid, path, text in rows
            if path and Path(path).exists() and (needs_ocr(Path(path), text) or text.startswith("[OCR 실패"))]


def ocr_file(con: sqlite3.Connection, file_id: str, on_page=None) -> int:
    """받아둔 파일 하나를 OCR해서 본문검색(fts)에 넣는다. 읽은 글자 수 반환."""
    r = con.execute("SELECT reg_no, path FROM files WHERE file_id=? AND status='ok'", (file_id,)).fetchone()
    if not r or not r[1] or not Path(r[1]).exists():
        raise WonmunError("파일이 저장 폴더에 없습니다")
    text = ocr_pdf(Path(r[1]), on_page)
    con.execute("DELETE FROM fts WHERE file_id=?", (file_id,))
    con.execute("INSERT INTO fts VALUES (?,?,?)", (file_id, r[0], text))
    con.commit()
    return len(text) - len(OCR_MARK)


def _to_recycle_bin(path: Path) -> None:
    """파일을 휴지통으로 보낸다(완전 삭제하지 않음)."""
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                    ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]
    FO_DELETE, FOF_ALLOWUNDO, FOF_NOCONFIRMATION, FOF_SILENT, FOF_NOERRORUI = 3, 0x40, 0x10, 0x4, 0x400
    op = SHFILEOPSTRUCTW(wFunc=FO_DELETE, pFrom=str(path.resolve()) + "\0",
                         fFlags=FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI)
    rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if rc != 0 or path.exists():
        raise WonmunError(f"휴지통으로 옮기지 못했습니다 (코드 {rc})")


def delete_files(con: sqlite3.Connection, file_ids: list[str]) -> list[dict]:
    """받은 파일을 휴지통으로 보내고 DB 기록(files, fts)도 지운다."""
    out = []
    for fid in file_ids:
        r = con.execute("SELECT file_nm, path FROM files WHERE file_id=?", (fid,)).fetchone()
        if not r:
            continue
        p = Path(r[1]) if r[1] else None
        try:
            if p and p.is_file():
                _to_recycle_bin(p)
            con.execute("DELETE FROM files WHERE file_id=?", (fid,))
            con.execute("DELETE FROM fts WHERE file_id=?", (fid,))
            out.append({"file": r[0], "status": "삭제"})
        except Exception as e:  # noqa: BLE001
            out.append({"file": r[0], "status": "실패", "error": str(e)})
    con.commit()
    return out


def _on_this_pc(p: Path) -> bool:
    """이 PC의 경로인가. 드라이브가 없거나 C:\\Users\\<다른이름> 아래면 다른 PC에서 받은 것으로 본다."""
    if not Path(p.anchor).exists():
        return False
    parts = p.parts
    if len(parts) > 2 and parts[1].lower() == "users":
        return Path(*parts[:3]).exists()
    return True


def stale_files(con: sqlite3.Connection) -> tuple[list[str], int]:
    """탐색기에서 지워진 파일의 기록 = 이 PC 경로인데 파일이 없는 것.
    다른 PC에서 받은 기록(DB가 구글 드라이브로 공유됨)은 건드리지 않는다.
    (지울 file_id 목록, 다른 PC 것으로 보여 남겨둔 수) 반환."""
    stale, other = [], 0
    for fid, path in con.execute("SELECT file_id, path FROM files WHERE status='ok'"):
        p = Path(path or "")
        if not path or p.exists():
            continue
        if _on_this_pc(p):
            stale.append(fid)
        else:
            other += 1
    return stale, other


def cleanup_files(con: sqlite3.Connection) -> int:
    stale, _ = stale_files(con)
    for fid in stale:
        con.execute("DELETE FROM files WHERE file_id=?", (fid,))
        con.execute("DELETE FROM fts WHERE file_id=?", (fid,))
    con.commit()
    return len(stale)


def _hwp_text(path: Path) -> str:
    """HWP 5.0 본문 텍스트를 올레(OLE) 구조에서 직접 읽는다(표/도형 안 글자는 일부 빠질 수 있음)."""
    import struct
    import zlib

    import olefile
    ole = olefile.OleFileIO(str(path))
    header = ole.openstream("FileHeader").read()
    compressed = bool(header[36] & 1)
    out = []
    for entry in ole.listdir():
        if entry[0] != "BodyText":
            continue
        data = ole.openstream(entry).read()
        if compressed:
            data = zlib.decompress(data, -15)
        i = 0
        while i + 4 <= len(data):
            hdr = struct.unpack("<I", data[i:i + 4])[0]
            tag, size = hdr & 0x3FF, (hdr >> 20) & 0xFFF
            i += 4
            if size == 0xFFF:
                size = struct.unpack("<I", data[i:i + 4])[0]
                i += 4
            if tag == 67:  # HWPTAG_PARA_TEXT
                out.append(_para_text(data[i:i + size]))
            i += size
    return "\n".join(out)


# 한글 문단 텍스트 안의 제어문자: 아래 코드들은 뒤에 7글자(14바이트) 부가정보가 붙는다
_INLINE_CTRL = {1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23}


def _para_text(buf: bytes) -> str:
    out, i, n = [], 0, len(buf) // 2
    while i < n:
        code = int.from_bytes(buf[2 * i:2 * i + 2], "little")
        if code in _INLINE_CTRL:
            i += 8
            continue
        if code == 10 or code == 13:
            out.append("\n")
        elif code >= 32:
            out.append(chr(code))
        i += 1
    return "".join(out)


# ---------- DB ----------

def init_fts(con: sqlite3.Connection) -> None:
    con.execute("""CREATE TABLE IF NOT EXISTS files (
        file_id TEXT PRIMARY KEY, reg_no TEXT, file_nm TEXT, kind TEXT, path TEXT,
        status TEXT, error TEXT, fetched_at TEXT)""")
    con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(file_id UNINDEXED, reg_no UNINDEXED, text)")
    con.commit()


def display_name(row: dict, file_nm: str, with_prefix: bool = True) -> str:
    """저장 파일명: '[학교] 제목 - 원래파일명' (본문 PDF는 '[학교] 제목.pdf')."""
    if not with_prefix:
        return file_nm
    school, title = row.get("school") or "", row.get("title") or ""
    if file_nm.startswith("결재문서본문"):
        return f"[{school}] {title}{Path(file_nm).suffix}"
    return f"[{school}] {title} - {file_nm}"


def download_doc(con: sqlite3.Connection, row: dict, *, attachments: bool = False,
                 session: requests.Session | None = None, file_ids: set[str] | None = None,
                 out_dir: Path | None = None, with_prefix: bool = True, vo: dict | None = None,
                 force: bool = False) -> list[dict]:
    """문서 한 건의 파일을 받고 본문 텍스트를 FTS에 넣는다. 결과 목록 반환.

    file_ids를 주면 그 파일만, 아니면 본문(+attachments면 첨부)을 받는다.
    out_dir을 주지 않으면 설정된 저장 폴더를 쓴다.
    """
    s = session or opengo._session()
    init_fts(con)
    vo = vo or fetch_detail(s, row["reg_no"], row["produced"], row["instt_se"])
    out_dir = out_dir or download_dir()
    results = []
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    for f in vo.get("fileList") or []:
        kind = f.get("fileSeDc", "")
        if file_ids is not None:
            if f["fileId"] not in file_ids:
                continue
        elif kind != "본문" and not attachments:
            continue
        done = con.execute("SELECT status, path FROM files WHERE file_id=?", (f["fileId"],)).fetchone()
        if done and done[0] == "ok" and not force and done[1] and Path(done[1]).exists():
            results.append({"file": f["fileNm"], "status": "이미 있음", "path": done[1]})
            continue
        try:
            path = download_file(s, vo, f, out_dir, save_as=display_name(row, f["fileNm"], with_prefix))
            text = extract_text(path)
            if needs_ocr(path, text):
                try:
                    text = ocr_pdf(path)
                except Exception as e:  # noqa: BLE001  OCR이 안 돼도 다운로드는 성공으로 둔다
                    text = f"[OCR 실패: {e}]"
            con.execute("INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?,?,?)",
                        (f["fileId"], row["reg_no"], f["fileNm"], kind, str(path), "ok", "", now))
            con.execute("DELETE FROM fts WHERE file_id=?", (f["fileId"],))
            con.execute("INSERT INTO fts VALUES (?,?,?)", (f["fileId"], row["reg_no"], text))
            results.append({"file": f["fileNm"], "status": "완료", "chars": len(text), "path": str(path)})
        except Exception as e:  # noqa: BLE001
            msg = str(e).splitlines()[0][:200]
            con.execute("INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?,?,?)",
                        (f["fileId"], row["reg_no"], f["fileNm"], kind, "", "fail", msg, now))
            results.append({"file": f["fileNm"], "status": "실패", "error": msg})
        con.commit()
        time.sleep(0.5)
    return results


def search_text(con: sqlite3.Connection, query: str, limit: int = 100) -> list[tuple]:
    """본문 전문검색. (reg_no, file_id, 발췌) 반환."""
    init_fts(con)
    q = " ".join(f'"{w}"' for w in query.split())
    return con.execute(
        "SELECT reg_no, file_id, snippet(fts, 2, '[', ']', '…', 24) FROM fts WHERE fts MATCH ? LIMIT ?",
        (q, limit)).fetchall()
