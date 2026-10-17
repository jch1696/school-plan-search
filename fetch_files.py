"""(GitHub Actions) 문서 한 건의 원문 파일을 전부 받아 zip 하나로 묶는다.

웹 페이지의 "파일 받기" → download.yml → 이 스크립트 → zip을 Release(tag: files)에 올림.
DB는 쓰지 않고(메모리 DB) docs/data.json에서 문서 정보를 찾는다.

실행:  python fetch_files.py <reg_no> <out_dir>     → <out_dir>/<reg_no>.zip
"""
from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

import export_web
import opengo
import wonmun


def main(reg_no: str, out_dir: Path) -> int:
    row = next((r for r in export_web.load()["rows"] if r["reg_no"] == reg_no), None)
    if not row:
        print(f"목록에 없는 문서: {reg_no}")
        return 1
    print(f"[{row['school']}] {row['title']}")
    con = opengo.init_db(":memory:")  # type: ignore[arg-type]
    files_dir = out_dir / reg_no
    results = wonmun.download_doc(con, row, attachments=True, out_dir=files_dir, with_prefix=True)
    got = [r for r in results if r["status"] == "완료"]
    for r in results:
        print(f"  {r['status']}: {r['file']} {r.get('error', '')}")
    if not got:
        print("받은 파일이 없습니다")
        return 1

    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"{reg_no}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for r in got:
            z.write(r["path"], Path(r["path"]).name)
        failed = [r for r in results if r["status"] == "실패"]
        if failed:  # 못 받은 파일은 이유를 안내문으로 넣는다
            note = "받지 못한 파일\n\n" + "\n".join(f"- {r['file']}: {r.get('error', '')}" for r in failed)
            z.writestr("_받지 못한 파일.txt", note)
    summary = {"reg_no": reg_no, "title": row["title"], "school": row["school"],
               "files": [{"name": r["file"], "status": r["status"], "error": r.get("error", "")} for r in results]}
    (out_dir / f"{reg_no}.json").write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")
    print(f"zip: {zip_path} ({zip_path.stat().st_size // 1024} KB, 파일 {len(got)}개)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], Path(sys.argv[2] if len(sys.argv) > 2 else "out")))
