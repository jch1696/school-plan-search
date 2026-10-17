# 개발메모 — 학교 계획서 검색기

최종 정리: 2026-10-17

사용법은 `README.md`, 이 문서는 **다음에 코드를 고칠 사람(나 또는 AI)이 알아야 할 것**을 적는다.

---

## 1. 목적

학교 업무 계획서를 쓸 때 다른 학교 계획서를 참고하려고 만든 도구.
정보공개포털(open.go.kr)의 교육청·학교 원문정보를 키워드로 모아 두고,
원하는 원문(본문 PDF, 첨부 HWP)만 골라 내려받고, 본문까지 검색한다.

## 2. 구조

```
[브라우저] static/index.html  ──fetch──▶  server.py (FastAPI, 127.0.0.1:8765)
                                              ├─ opengo.py  : 포털 목록 검색, docs 테이블 저장
                                              └─ wonmun.py  : 원문 다운로드, 텍스트 추출, 본문검색(FTS)
                                                     ▼
                                     opengo.db (SQLite)  +  저장 폴더(settings.json)
```

| 파일 | 역할 |
|---|---|
| `server.py` | API + 화면 제공. 오래 걸리는 작업(수집, 다운로드)은 스레드로 돌리고 `/api/jobs/{id}`로 진행률 조회 |
| `static/index.html` | 화면 전체(HTML/CSS/JS 한 파일, 외부 라이브러리 없음). 3단: 검색조건 / 목록 / 상세 |
| `opengo.py` | 세션 초기화, 원문정보 목록 검색, `docs` 저장 |
| `wonmun.py` | 상세 조회, 원문 다운로드 4단계, PDF/HWP/HWPX 텍스트 추출, `files`·`fts` 테이블 |
| `collect.py` + `keywords.txt` | 검색어 일괄 수집(명령줄) |
| `settings.json` | 저장 폴더(`download_dir`), 파일명 접두어(`prefix`) |
| `*.bat` | 더블클릭 실행용. **cp949로 저장해야 함**(아래 함정 참고) |
| `export_web.py` | `docs/data.json`(폰·웹용) 관리. PC: DB↔data.json 합쳐 내보내기. Actions: `collect` 모드로 포털 검색→data.json 추가 |
| `docs/index.html` | GitHub Pages 페이지(폰용). `data.json`을 통째로 받아 브라우저에서 거름. 포털 상세는 POST 폼으로 새 탭. "가져오기"는 GitHub API로 워크플로 실행 |
| `.github/workflows/collect.yml` | `workflow_dispatch(keyword, days)` → `export_web.py collect` → data.json 커밋. `concurrency: collect`로 직렬화 |

### DB 테이블 (`opengo.db`)
- `docs` : 목록 1건 = 문서 1건. PK `reg_no`(=PRDCTN_INSTT_REGIST_NO). `open_type`은 "공개"/"부분공개".
- `files` : 내려받은 파일. PK `file_id`. `status` = ok / fail, `path`, `error`.
- `fts` : FTS5 가상테이블 (`file_id`, `reg_no`, `text`). 본문검색용.

## 3. 정보공개포털 내부 동작 (직접 분석한 것)

> 공식 Open API **없음**. data.go.kr에도 없음(2026-10 확인). 브라우저 요청을 재현한다.
> 사이트 개편 시 여기부터 다시 확인할 것.

### 3-1. 세션 (중요)
1. `GET /othicInfo/infoList/orginlInfoList.do` → 쿠키(JSESSIONID 등)
2. `POST /common/util/setFingerPrint.ajax` (`fingerprint`=아무 SHA256 대문자 hex)
   - **이걸 안 하면 모든 검색이 `code: 491`로 거부됨.** 원래는 페이지 JS `generateUFP()`가 브라우저 지문을 보냄.
- AJAX 요청엔 `X-Requested-With: XMLHttpRequest`, `Referer`, form-urlencoded 헤더.
- XSRF-TOKEN 쿠키/헤더는 검색에는 필요 없었음.

### 3-2. 목록 검색
`POST /othicInfo/infoList/orginlInfoList.ajax`
- 주요 파라미터: `kwd`, `preKwds`(=kwd), `reSrchFlag=off`, `insttSeCd`(C 중앙, W 광역, B 기초, **E 교육청**, P 공공기관),
  `eduYn=Y`(초·중·고 포함), `startDate`/`endDate`(yyyymmdd, **최대 1년**), `viewPage`, `rowPage`(50까지 씀), `sort=s`
- 응답 `result.rtnList[]`, `result.rtnTotal`, `result.code`("200" 정상)
- 쓰는 필드: `PRDCTN_INSTT_REGIST_NO`, `INFO_SJ`, `NFLST_CHRG_DEPT_NM`, `CHRG_DEPT_NM`, `PRDCTN_DT`, `P_DATE`,
  `OTHBC_SE_CD`(1 공개, 2 부분공개), `INSTT_SE_CD`, `FILE_NM`("|" 구분, "[본문] ..." 형식)
- **줄임말과 정식 이름의 결과가 다름**: 최근 1년 기준 `월항초` 85건, `월항초등학교` 107건(2026-10-17).
  → `server.expand_keyword()`가 3글자 이상 + 초/중/고로 끝나는 단어를 `초등학교/중학교/고등학교`로 늘려 **한 번 더** 검색한다.
  엉뚱하게 늘어나도("결과보고등학교") 0건이라 해 없음.
- 포털 사이트 화면에서 **기관선택**(예: 성주교육지원청)을 하면 결과가 크게 줄어듦(같은 `월항초`가 1건). 우리 수집은 기관 지정 없이 교육청 전체(`insttSeCd=E`).
- **앱 안의 목록 검색은 글자 포함**(`title LIKE %단어%`). 그래서 같은 검색어라도 포털 화면보다 앱 결과가 많을 수 있음.
- 포털 기간은 최대 1년. 앱의 "기간"은 받아둔 DB를 거르는 것이라 별개.

### 3-3. 상세
`POST /othicInfo/infoList/infoListDetl.do` (`prdnNstRgstNo`, `prdnDt`, `nstSeCd`, `title=원문정보`)
- HTML 안에 `var result = {...};` JSON이 박혀 있음 → 정규식으로 꺼냄 → `openCateSearchVO`
- `openCateSearchVO.fileList[]` : `fileId`, `fileNm`, `fileSeDc`(본문/첨부), `fileOppYn`(N=비공개), `fileByteNum`

### 3-4. 원문 다운로드 (사이트 JS의 wonmunStep1~7을 따라 함)
1. `POST /util/wonmunUtils/wonmunFileRequest.ajax` → `esbFilePath`, `fileName`, `orglPrdnNstCd`
   - `error_code` 0/00 → 2번으로, **01 → 이미 준비됨, 2번 건너뜀**, 그 외 → 실패(메시지 그대로 표시)
2. `POST /util/wonmunUtils/wonmunFileFilter.ajax` (개인정보 필터, 파라미터 많음 → `wonmun.py` 참고)
   - `(05) 처리량이 많아 지연` → 8초 간격 최대 4회 재시도
3. (PDF 변환 `wonmunChangePdf.ajax`는 isPdf=Y일 때만. 우리는 isPdf=N으로 원본을 받음)
4. `POST /util/wonmunUtils/wonmunFileDownload.down` (`gubun=esbFilePath`) → 파일 바이트
- ⚠ `wonmunFileResponse.ajax`는 사이트 JS에 함수는 있지만 실제 흐름에서 안 씀. 넣었더니 "시스템 오류" 남.

### 3-5. 자주 보는 실패
- "비공개 파일로 열람이 불가" : 학교가 첨부를 비공개로 올림. 사이트에서도 못 받음. 본문 PDF는 대개 됨.
- "파일이 존재하지 않아" : 제공기관 쪽 파일 누락. 우리 쪽 문제 아님.

## 4. 텍스트 추출
- PDF: `pypdf`. 글자가 20자 미만이면 스캔본으로 보고 **OCR**(`wonmun.ocr_pdf`):
  PyMuPDF(`fitz`)로 쪽마다 200dpi PNG → **Windows 내장 OCR**(`winrt-Windows.Media.Ocr`, 한국어) → 최대 30쪽.
  설치할 것 없음(한국어 Windows면 `ko` 인식기가 있음). 1쪽 약 0.5초, 글자 몇 개 틀리는 정도.
  OCR 글자는 fts에 `[OCR] ` 접두어로 저장해 화면에서 "틀릴 수 있음" 표시. 실패하면 `[OCR 실패: …]` 저장(다운로드는 성공 처리).
  다운로드 때 자동으로 하고, 예전 파일은 왼쪽 아래 "OCR로 읽기"(`/api/ocr`)나 미리보기의 버튼으로.
- HWPX: zip 안 `Contents/section*.xml` 태그 제거.
- HWP 5.0: `olefile`로 `BodyText/Section*` 스트림 → (FileHeader 압축 플래그면) `zlib -15` 해제 → 레코드 태그 67(PARA_TEXT)만 UTF-16LE로 읽음.
  인라인 제어문자(코드 1~23 중 일부)는 뒤 14바이트 부가정보까지 건너뛰어야 깨진 한자(`捤獥` 등)가 안 나옴 → `_para_text()`.
  표 안 글자는 일부 빠질 수 있음.

## 5. 함정 / 환경 메모 (Windows)
- **SSL 오류**(self-signed certificate in chain): PC 보안프로그램이 HTTPS를 검사함 → `truststore.inject_into_ssl()`로 Windows 인증서 저장소 사용.
- **.bat 한글 깨짐**: cmd는 cp949. bat을 UTF-8로 저장하면 명령이 깨져 실행 안 됨 → Python으로 `encode("cp949")`해서 저장.
- SQLite 연결은 스레드를 넘기면 오류 → 요청마다 `db()`로 새로 열고 `check_same_thread=False`.
- 폴더 선택 창: 서버가 같은 PC라서 `tkinter.filedialog`로 띄움(별도 스레드, topmost).
- 콘솔 출력 한글 깨짐: 명령줄에서 돌릴 때 `PYTHONIOENCODING=utf-8`.
- Python 3.14, 패키지: requests, truststore, fastapi, uvicorn, pypdf, olefile, PyMuPDF, winrt-Windows.Media.Ocr 외 winrt-*(Foundation, Globalization, Graphics.Imaging, Storage.Streams).
- **DB가 구글 드라이브로 여러 PC에서 공유됨**: `files.path`에 다른 PC 경로(`C:\Users\user\...`)가 섞여 있음.
  기록 정리(`wonmun.stale_files`)는 드라이브나 `C:\Users\<이름>` 폴더가 이 PC에 없으면 "다른 PC 것"으로 보고 건드리지 않음.
- 파일 삭제는 `SHFileOperationW`로 **휴지통**에 보냄(되살리기 가능). 기록(files·fts)은 지워짐.

## 6. 바뀐 이력
1. Open API 찾아봄 → 없음 확인 → 사이트 요청 재현으로 방향 전환
2. 목록 수집기(`opengo.py`) + Streamlit 화면
3. 원문 다운로드 + 본문 전문검색(`wonmun.py`)
4. 더블클릭 실행 bat
5. 저장 폴더 설정, 파일 골라 받기, 파일명 `[학교] 제목 - 원래이름`
6. **Streamlit 제거** → FastAPI + 직접 만든 HTML 3단 화면 (사용자: "화면이 직관적이지 않다")
7. 공개/부분공개 필터와 표시
8. (2026-10-17) 목록 300건 제한 → 100건씩 "더 보기"(`/api/docs`의 `offset`/`limit`, `total`은 전체 건수).
   "목록 전체 본문 받기"는 안 펼친 것까지 전체 대상. 포털 가져오기에 줄임말 확장(3-2 참고).
9. (2026-10-17) 받은 파일 삭제(휴지통) + 탐색기에서 지운 파일 기록 정리(`/api/files/delete`, `/api/files/stale`, `/api/files/cleanup`).
   스캔 PDF OCR(4절).
10. (2026-10-17) GitHub 공개 저장소 `jch1696/school-plan-search` + GitHub Pages(`docs/` 폴더).
    **정적 페이지는 포털을 직접 검색 못 함**(CORS + 지문 세션) → PC DB를 내보낸 `data.json`(약 1.5MB)을 검색.
11. (2026-10-17) 폰에서 "가져오기": **GitHub Actions(해외 IP)에서 포털 접속 됨** 확인(졸업식 404건 3.6초).
    폰 페이지 → GitHub API `workflow_dispatch`(폰 localStorage의 fine-grained 토큰, Actions: write) → 포털 검색 → data.json 커밋
    → 페이지가 run 상태를 폴링하고 끝나면 `raw.githubusercontent.com`에서 새 data.json을 받음(Pages CDN 캐시 10분 회피).
    목록이 두 곳(PC DB, data.json)에서 늘어나므로 `폰용 목록 올리기.bat`은 `git pull --rebase` → data.json의 새 문서를 DB에 넣기 → DB 전체 내보내기 → push.
    공통 함수(`level_of`, `region_of`, `expand_keyword`)는 Actions에 fastapi가 없어서 `server.py`→`opengo.py`로 옮김.

## 7. 다음에 할 만한 것
- [x] 스캔 PDF OCR (2026-10-17, Windows 내장 OCR)
- [ ] 여러 계획서 비교 표 / AI 요약 (목적·일정·예산·안전 항목 뽑기)
- [ ] `collect.py`를 Windows 작업 스케줄러로 매일 실행
- [x] 받은 파일 삭제/정리 기능 (2026-10-17)
- [x] 목록 300건 제한 → 페이지 넘김 (2026-10-17)
- [x] 포털 검색어 줄임말 확장 (2026-10-17)
- [ ] DB(`opengo.db`)가 구글 드라이브 동기화 폴더 안에 있음. 여러 PC에서 동시에 쓰면 충돌 위험 → 필요하면 로컬로 옮기기
- [x] GitHub Pages에 목록 공개 + 폰에서 포털 가져오기 (2026-10-17). 다운로드는 PC 전용 유지. 매일 자동 수집은 안 하기로.
- [ ] `data.json`이 커지면(수만 건) 연도별로 나누거나 gzip 고려

## 8. 빠른 점검 명령
```bash
python server.py                       # 화면 실행 (http://localhost:8765)
python opengo.py "학예회 계획" 30       # 수집만 테스트 (code 491이면 3-1 확인)
python -c "import opengo,wonmun; c=opengo.init_db(); print(wonmun.search_text(c,'예산')[:1])"
```
