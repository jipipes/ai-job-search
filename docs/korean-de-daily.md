# 국내 채용공고 일일 수집과 Notion 지원 관리

저장소 루트에서 실행합니다.

```bash
.venv/bin/python -m scripts.korean_de_jobs daily
.venv/bin/python -m scripts.korean_de_jobs jobs
```

`daily`는 더파운더즈 공식 사이트맵을 확인해 신규 공고와 본문이 변경된 공고를 보여줍니다. 실행당 최대 15건을 읽으며, `remaining`은 다음 실행에서 처리할 건수입니다. `jobs`는 이미 수집한 공고를 보여줍니다. SQLite 파일은 `results/korean_de_jobs.sqlite3`에 저장됩니다. 사이트맵 수정 시각이 그대로인 공고도 7일 후에 재확인합니다.

## 신규 데이터 직무 공고 Slack 알림

`daily` 결과의 각 공고에는 `relevant`, `data_matches`, `min_years`가 붙습니다. 직무명이 데이터 엔지니어·애널리틱스 엔지니어·백엔드·FDE 등으로 섞여 있어 제목 대신 주요 업무·자격 요건·우대 사항의 내용을 봅니다.

- `data_matches`: 본문에서 찾은 데이터 작업 표현(SQL, Airflow, dbt, Spark, Kafka, 파이프라인, DW/DL 등). 2개 이상이면 데이터 직무로 봅니다.
- `min_years`: 자격 요건(우대 사항 제외)에 적힌 최소 경력 연수. 3년 이상이면 제외합니다.

`SLACK_NEW_POSTINGS_WEBHOOK_URL`을 설정하면 `relevant`인 공고를 한 메시지로 묶어 보냅니다. 같은 공고는 한 번만 보내며 기록은 SQLite의 `posting_notifications`에 남깁니다. 기준은 `scripts/posting_filter.py`의 `DATA_TERMS`, `MIN_DATA_MATCHES`, `MAX_YEARS`에서 조정합니다.

## 윈도우에서 매일 실행

Windows 작업 스케줄러에서 기본 작업을 만들고 매일 원하는 시각에 시작하도록 설정합니다. 프로그램은 `wsl.exe`, 인수는 아래처럼 지정합니다. Ubuntu 사용자명이나 설치한 배포판 이름이 다르면 변경합니다.

```text
-d Ubuntu -u soojin --cd /home/soojin/projects/job-search-agent -- bash -c "set -a; source ~/.config/job-tracker.env && exec .venv/bin/python -m scripts.korean_de_jobs daily > results/korean_de_daily.json"
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

## 기존 Job Tracker 데이터 소스에 맞추기

[기존 Job Tracker](https://app.notion.com/p/Job-Tracker-153c45a9b32a8008988bd7ef5537de24)의 데이터베이스를 연결에 공유한 뒤 해당 데이터 소스 ID를 설정합니다. 링크의 페이지 ID는 데이터 소스 ID와 다를 수 있습니다. 속성 이름이나 값이 위 예시와 다르면 읽기 전용 명령으로 확인합니다.

```bash
.venv/bin/python -m scripts.korean_de_jobs notion-schema
```

기존 속성에 맞춰 다음 환경 변수 중 필요한 값만 설정합니다.

| 환경 변수 | 기본값 또는 의미 |
|---|---|
| `JOB_TRACKER_TITLE` | 공고명 |
| `JOB_TRACKER_COMPANY` | 회사. 빈 값이면 회사 속성 없이 제목을 `회사 / 공고명`으로 저장 |
| `JOB_TRACKER_URL` | 공고 URL |
| `JOB_TRACKER_STAGE` | 지원 단계 |
| `JOB_TRACKER_STAGE_TYPE` | `select`. Notion 상태 속성은 `status` |
| `JOB_TRACKER_PLANNED` | 지원 예정 |
| `JOB_TRACKER_DEADLINE` | 마감일 |
| `JOB_TRACKER_APPLIED` | 선택 사항. 지원 여부를 나타내는 속성 이름. 날짜·선택·체크박스 등 값이 채워진 공고는 알림에서 제외. 예전 이름 `JOB_TRACKER_APPLIED_DATE`도 인식 |

기존 Job Tracker의 `Job DB`(데이터 소스 `153c45a9-b32a-81d4-a652-000b4c2d862c`)는 아래처럼 매핑합니다. `Status=Document`이고 `지원`이 비어 있으면 아직 지원하지 않은 공고로 보고, 서류를 내면 `지원`을 `서류`로 바꿉니다. `Schedule`은 마감일로 사용합니다.

```bash
NOTION_DATA_SOURCE_ID=153c45a9-b32a-81d4-a652-000b4c2d862c
JOB_TRACKER_TITLE=Name
JOB_TRACKER_COMPANY=
JOB_TRACKER_URL=JD
JOB_TRACKER_STAGE=Status
JOB_TRACKER_STAGE_TYPE=select
JOB_TRACKER_PLANNED=Document
JOB_TRACKER_DEADLINE=Schedule
JOB_TRACKER_APPLIED=지원
```

## Slack 마감 알림

Slack 앱에서 Incoming Webhook을 추가하고 받을 채널을 지정합니다. 웹훅 URL을 `SLACK_WEBHOOK_URL`로 설정합니다. 자동 실행 시 Notion 인증, 속성 매핑, Slack 웹훅은 작업 스케줄러가 읽을 수 있는 환경에 설정합니다. 토큰과 웹훅이 들어 있는 파일은 Git 저장소 밖에 보관합니다.

```bash
.venv/bin/python -m scripts.korean_de_jobs remind --dry-run
.venv/bin/python -m scripts.korean_de_jobs remind
```

`--dry-run`으로 후보를 확인한 뒤, 발송 명령을 별도의 Windows 작업에 추가해 매일 실행합니다. 예를 들어 Ubuntu의 `~/.config/job-tracker.env`에 권한 `600`을 부여해 환경 변수를 보관했다면 작업 스케줄러의 `wsl.exe` 인수를 아래처럼 설정할 수 있습니다.

```text
-d Ubuntu -u soojin --cd /home/soojin/projects/job-search-agent -- bash -c "set -a; source ~/.config/job-tracker.env && exec .venv/bin/python -m scripts.korean_de_jobs remind"
```

알림은 한국 날짜 기준 D-7, D-5, D-3, D-1, 마감 당일에 보냅니다. 매 실행마다 Notion의 현재 상태와 마감일을 읽습니다. `채용 시 마감`처럼 마감일이 비어 있거나 지원한 공고는 대상에서 제외됩니다. 발송이 성공한 공고·마감일·알림 시점 조합은 같은 SQLite 파일에 기록합니다. PC가 꺼져 실행을 놓친 날의 알림은 소급 발송되지 않습니다.
