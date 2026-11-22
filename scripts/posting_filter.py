"""Decide from a posting's content whether it is data work worth announcing.

Job titles in Korea mix 데이터 엔지니어, 애널리틱스 엔지니어, 데이터 중심 백엔드, FDE and
more, so the decision reads duties and requirements instead of the title.
"""

import re

import requests

from scripts.job_tracker_notion import check_webhook

# Each entry: label shown in Slack -> regex searched case-insensitively in the body.
DATA_TERMS = {
    "SQL": r"(?<![a-z])(sql|쿼리)(?![a-z])",
    "ETL/ELT": r"(?<![a-z])(etl|elt)(?![a-z])",
    "파이프라인": r"(데이터\s*)?파이프라인|data\s*pipeline",
    "Airflow": r"airflow",
    "dbt": r"(?<![a-z])dbt(?![a-z])",
    "Spark": r"spark|databricks",
    "Kafka": r"kafka|kinesis|pub/?sub",
    "Flink": r"flink",
    "DW/DL": r"데이터\s*(웨어하우스|레이크|마트)|data\s*(warehouse|lake|mart)|lakehouse|레이크하우스",
    "BigQuery": r"bigquery|빅쿼리",
    "Snowflake": r"snowflake",
    "Redshift": r"redshift|athena|glue",
    "Hadoop/Hive": r"hadoop|hive|trino|presto",
    "데이터 모델링": r"데이터\s*모델링|data\s*model(l)?ing|dimensional|스타\s*스키마",
    "CDC/스트리밍": r"(?<![a-z])cdc(?![a-z])|debezium|스트리밍|streaming",
    "배치 처리": r"배치\s*(처리|작업|파이프라인)|batch\s*(processing|job|pipeline)",
    "데이터 플랫폼": r"데이터\s*(플랫폼|인프라|엔지니어링)|data\s*(platform|infrastructure|engineering)",
    "데이터 엔지니어": r"데이터\s*엔지니어|data\s*engineer|analytics\s*engineer|애널리틱스\s*엔지니어",
    "데이터 품질": r"데이터\s*(품질|정합성|거버넌스)|data\s*(quality|governance)",
}
MIN_DATA_MATCHES = 2
MAX_YEARS = 3  # postings that require this many years or more are dropped

_YEAR_PATTERNS = [
    r"(\d+)\s*(?:~|-|–)\s*\d+\s*년",          # 3~5년
    r"(\d+)\s*년\s*(?:이상|차\s*이상|\+)",      # 3년 이상, 5년차 이상
    r"경력\s*(\d+)\s*년",                        # 경력 3년
    r"(\d+)\s*\+?\s*years?",                     # 3+ years, 5 years
]


def _lines(job, *keys):
    for key in keys:
        value = job.get(key) or []
        yield from ([value] if isinstance(value, str) else value)


def data_matches(job):
    text = "\n".join([job.get("title", ""), *_lines(
        job, "duties", "requirements", "preferred_requirements")]).lower()
    return [label for label, pattern in DATA_TERMS.items() if re.search(pattern, text)]


def min_years_required(job):
    """Smallest experience requirement (in years) stated in the required qualifications."""
    found = []
    for line in [job.get("title", ""), *_lines(job, "requirements")]:
        lowered = line.lower()
        if not re.search(r"경력|경험|experience|년차|이상|\+", lowered):
            continue
        for pattern in _YEAR_PATTERNS:
            found += [int(value) for value in re.findall(pattern, lowered)]
    return min(found) if found else None


def review(job):
    matches = data_matches(job)
    years = min_years_required(job)
    relevant = len(matches) >= MIN_DATA_MATCHES and (years is None or years < MAX_YEARS)
    return {"relevant": relevant, "data_matches": matches, "min_years": years}


def notify_new_postings(db, session, webhook, postings):
    """Post one Slack digest of relevant postings not announced before."""
    check_webhook(webhook, "SLACK_NEW_POSTINGS_WEBHOOK_URL")
    db.execute("CREATE TABLE IF NOT EXISTS posting_notifications (url TEXT PRIMARY KEY)")
    fresh = [item for item in postings if not db.execute(
        "SELECT 1 FROM posting_notifications WHERE url=?", (item["url"],)).fetchone()]
    if not fresh:
        return {"sent": 0}
    lines = [f"새 데이터 직무 공고 {len(fresh)}건"]
    for item in fresh:
        deadline = item.get("deadline_date") or "채용 시 마감"
        lines.append(f"• {item['title']} · {deadline}\n  매칭: {', '.join(item['data_matches'])}\n  {item['url']}")
    try:
        response = session.post(webhook, timeout=15, allow_redirects=False, json={
            "text": "\n".join(lines), "mrkdwn": False, "unfurl_links": False,
        })
    except requests.RequestException:
        return {"sent": 0, "error": "Slack request failed"}
    if response.status_code != 200 or response.text.strip() != "ok":
        return {"sent": 0, "error": f"Slack HTTP {response.status_code}"}
    db.executemany("INSERT OR IGNORE INTO posting_notifications VALUES (?)",
                   [(item["url"],) for item in fresh])
    return {"sent": len(fresh)}
