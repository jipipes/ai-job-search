# 국내 채용공고 일일 수집과 Notion 지원 관리

저장소 루트에서 실행합니다.

```bash
.venv/bin/python -m scripts.korean_de_jobs daily
.venv/bin/python -m scripts.korean_de_jobs jobs
```

`daily`는 더파운더즈 공식 사이트맵을 확인해 신규 공고와 본문이 변경된 공고를 보여줍니다. 실행당 최대 15건을 읽으며, `remaining`은 다음 실행에서 처리할 건수입니다. `jobs`는 이미 수집한 공고를 보여줍니다. SQLite 파일은 `results/korean_de_jobs.sqlite3`에 저장됩니다. 사이트맵 수정 시각이 그대로인 공고도 7일 후에 재확인합니다.

## 윈도우에서 매일 실행

Windows 작업 스케줄러에서 기본 작업을 만들고 매일 원하는 시각에 시작하도록 설정합니다. 프로그램은 `wsl.exe`, 인수는 아래처럼 지정합니다. Ubuntu 사용자명이나 설치한 배포판 이름이 다르면 변경합니다.

```text
-d Ubuntu -u soojin --cd /home/soojin/projects/job-search-agent -- bash -c ".venv/bin/python -m scripts.korean_de_jobs daily > results/korean_de_daily.json"
```

실행 결과는 프로젝트의 `results/korean_de_daily.json`에서 확인합니다. PC가 꺼져 있던 날도 다음 로그인 이후 실행되길 원하면 작업 설정에서 예약된 시작을 놓친 경우 가능한 한 빨리 작업을 실행하는 옵션을 켭니다.

## 지원하기로 한 공고만 Notion에 등록

Notion 내부 연결(Integration)을 만들고 지원 관리 데이터베이스에서 해당 연결에 읽기 및 삽입 권한을 부여합니다. 아래 이름과 형식으로 속성을 만듭니다.

| 이름 | 형식 |
|---|---|
| 공고명 | 제목 |
| 회사 | 텍스트 |
| 공고 URL | URL |
| 지원 단계 | 선택 |
| 마감일 | 날짜 |

`지원 단계` 선택지는 `지원 예정`, `지원 완료`, `서류`, `코딩 테스트`, `면접`, `최종 합격`, `불합격`을 사용할 수 있습니다. 등록 후 단계는 Notion에서 변경합니다.

Ubuntu에서 현재 터미널에만 연동 토큰과 데이터 소스 ID를 설정합니다. 데이터베이스 ID와 데이터 소스 ID는 별도 값입니다.

```bash
read -rsp 'Notion token: ' NOTION_TOKEN
echo
export NOTION_TOKEN
read -rp 'Notion data source ID: ' NOTION_DATA_SOURCE_ID
export NOTION_DATA_SOURCE_ID
```

일일 결과의 `id` 또는 공고의 공식 URL을 사용해 등록합니다.

```bash
.venv/bin/python -m scripts.korean_de_jobs track cCAd5DPW
```

처음 등록하면 상태는 `지원 예정`입니다. 이미 같은 공고 URL이 있으면 기존 Notion 페이지 주소를 반환합니다. 공고를 등록한 뒤 지원서 제출은 해당 채용 사이트에서 진행합니다.
