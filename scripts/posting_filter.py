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
# Neighbouring work a data-engineering newcomer can still apply to (AI, ML, backend,
# analytics, cloud). These count toward relevance but mark the posting as "인접".
ADJACENT_TERMS = {
    "Python": r"python|파이썬|pandas|numpy",
    "AI/ML": r"머신\s*러닝|딥\s*러닝|machine\s*learning|deep\s*learning|(?<![a-z])(ml|mlops|llm|rag)(?![a-z])"
             r"|pytorch|tensorflow|ai\s*(엔지니어|engineer|모델|model|개발|서비스|에이전트|agent|플랫폼|research|리서치)",
    "백엔드": r"백엔드|back[\s-]?end|서버\s*개발|server\s*develop|(?<![a-z])api(?![a-z])",
    "JVM/Go": r"(?<![a-z])(java|kotlin|spring|go|golang|scala)(?![a-z])",
    "클라우드": r"(?<![a-z])(aws|gcp|azure)(?![a-z])|클라우드|cloud",
    "컨테이너": r"docker|kubernetes|(?<![a-z])k8s(?![a-z])|컨테이너",
    "데이터 분석": r"데이터\s*(분석|사이언스|사이언티스트)|data\s*(analy|scien)|통계|statistic",
    "BI/대시보드": r"(?<![a-z])bi(?![a-z])|tableau|looker|superset|power\s*bi|대시보드|dashboard",
    "FDE/솔루션": r"forward\s*deployed|(?<![a-z])fde(?![a-z])|솔루션\s*엔지니어|solutions?\s*engineer",
    "개발 직무": r"개발자|소프트웨어\s*엔지니어|software\s*engineer|developer",
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


def _body(job):
    return "\n".join([job.get("title", ""), *_lines(
        job, "duties", "requirements", "preferred_requirements")]).lower()


def _matches(terms, text):
    return [label for label, pattern in terms.items() if re.search(pattern, text)]


def data_matches(job):
    return _matches(DATA_TERMS, _body(job))


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
    """Keep anything a data/AI/backend newcomer could plausibly apply to.

    "데이터" when two or more data-engineering terms appear; "인접" when data and
    neighbouring tech terms together reach two. Three or more required years drops it.
    """
    text = _body(job)
    data = _matches(DATA_TERMS, text)
    adjacent = _matches(ADJACENT_TERMS, text)
    years = min_years_required(job)
    if len(data) >= MIN_DATA_MATCHES:
        category = "데이터"
    elif len(data) + len(adjacent) >= MIN_DATA_MATCHES:
        category = "인접"
    else:
        category = None
    relevant = category is not None and (years is None or years < MAX_YEARS)
    return {"relevant": relevant, "category": category,
            "data_matches": data + adjacent, "min_years": years}


def notify_new_postings(db, session, webhook, postings):
    """Post one Slack digest of relevant postings not announced before."""
    check_webhook(webhook, "SLACK_NEW_POSTINGS_WEBHOOK_URL")
    db.execute("CREATE TABLE IF NOT EXISTS posting_notifications (url TEXT PRIMARY KEY)")
    fresh = [item for item in postings if not db.execute(
        "SELECT 1 FROM posting_notifications WHERE url=?", (item["url"],)).fetchone()]
    if not fresh:
        return {"sent": 0}
    fresh.sort(key=lambda item: item.get("category") != "데이터")
    lines = [f"새 지원 후보 공고 {len(fresh)}건"]
    for item in fresh:
        deadline = item.get("deadline_date") or "채용 시 마감"
        label = item.get("category") or "데이터"
        title = item["title"]
        company = item.get("company")
        if company and item.get("source", "thefounders") != "thefounders" and company not in title:
            title = f"{company} / {title}"
        lines.append(f"• [{label}] {title} · {deadline}\n"
                     f"  매칭: {', '.join(item['data_matches'])}\n  {item['url']}")
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
