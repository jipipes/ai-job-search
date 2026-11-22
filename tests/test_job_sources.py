from contextlib import closing
from datetime import datetime
import sqlite3
from unittest.mock import Mock

from scripts import job_sources as sources
from scripts import korean_de_jobs as jobs


def response(payload):
    reply = Mock()
    reply.json.return_value = payload
    return reply


def test_jumpit_skips_senior_listings_and_maps_detail(monkeypatch):
    monkeypatch.setattr(sources, "JUMPIT_CATEGORIES", (19,))
    listing = {"result": {"totalCount": 2, "positions": [
        {"id": 1, "title": "데이터 엔지니어", "companyName": "핀다", "jobCategory": "빅데이터 엔지니어",
         "techStacks": ["Airflow", "Spark"], "newcomer": False, "minCareer": 1,
         "closedAt": "2026-12-01T23:59:59", "alwaysOpen": False},
        {"id": 2, "title": "Senior DE", "companyName": "X", "newcomer": False, "minCareer": 5},
    ]}}
    detail = {"result": {"responsibility": "• Airflow 파이프라인 운영\n• SQL 모델링",
                         "qualifications": "• Python 활용", "preferredRequirements": "",
                         "techStacks": [{"stack": "Airflow"}], "newcomer": False, "minCareer": 1,
                         "closedAt": "2026-12-01T23:59:59", "alwaysOpen": False}}
    session = Mock()
    session.get.side_effect = [response(listing), response(detail)]
    entries = list(sources.jumpit_entries(session, 3))
    assert [url for url, _, _ in entries] == [sources.JUMPIT_WEB + "1"]
    job = sources.jumpit_fetch(session, entries[0][2])
    assert job["company"] == "핀다" and job["deadline_date"] == "2026-12-01"
    assert job["duties"] == ["Airflow 파이프라인 운영", "SQL 모델링"]
    assert job["tech_stacks"] == ["Airflow"]


def test_saramin_maps_experience_and_open_ended_deadlines():
    item = {"url": "https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx=1",
            "company": {"detail": {"name": "카카오"}},
            "position": {"title": "데이터 엔지니어 신입", "job-code": {"name": "데이터엔지니어,SQL,Python"},
                         "experience-level": {"code": 1, "min": 0, "max": 0, "name": "신입"},
                         "job-type": {"name": "정규직"}},
            "keyword": "Airflow,Spark", "expiration-timestamp": "1796137199",
            "close-type": {"code": "1"}}
    job = sources.saramin_job(item)
    assert job["company"] == "카카오" and job["entry_level_eligible"]
    assert job["deadline_date"] == "2026-12-01"
    assert job["min_career"] == 0 and job["requirements"] == ["경력: 신입"]
    item["close-type"] = {"code": "2"}
    item["position"]["experience-level"] = {"code": 2, "min": 3, "max": 5, "name": "경력 3~5년"}
    job = sources.saramin_job(item)
    assert job["deadline_date"] is None and "경력 3년 이상" in job["requirements"]


def test_collect_source_fetches_only_new_or_changed_listings():
    fetched = []

    def fetch(item):
        fetched.append(item["id"])
        return {"title": item["title"], "entry_level_eligible": True, "source": "jumpit",
                "duties": ["Airflow 파이프라인", "SQL"]}

    now = datetime(2026, 11, 23, tzinfo=jobs.SEOUL)
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute("CREATE TABLE postings (url PRIMARY KEY, lastmod, checked_at, payload)")
        entries = [("u1", "v1", {"id": 1, "title": "DE"}), ("u2", "v1", {"id": 2, "title": "ML"})]
        first = jobs.collect_source(db, entries, now, 1, fetch, pause=0)
        assert first["remaining"] == 1 and fetched == [1]
        second = jobs.collect_source(db, entries, now, 5, fetch, pause=0)
        assert fetched == [1, 2] and second["changes"][0]["source"] == "jumpit"
        entries[0] = ("u1", "v2", {"id": 1, "title": "Data Engineer"})
        third = jobs.collect_source(db, entries, now, 5, fetch, pause=0)
        assert fetched == [1, 2, 1] and third["changes"][0]["change"] == "updated"
