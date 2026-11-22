from contextlib import closing
import sqlite3
from unittest.mock import Mock

from scripts.posting_filter import min_years_required, notify_new_postings, review


def job(title, duties=(), requirements=(), preferred=()):
    return {"title": title, "duties": list(duties), "requirements": list(requirements),
            "preferred_requirements": list(preferred)}


def test_content_decides_relevance_whatever_the_title():
    fde = job("Forward Deployed Engineer", duties=["고객사 데이터 파이프라인 구축", "Airflow 기반 배치 운영"])
    backend = job("백엔드 엔지니어", duties=["Kafka 이벤트 수집 서버 개발"], requirements=["SQL 활용 능력"])
    bi = job("[ANUA] BI & Data Engineer 채용전환형 인턴",
             requirements=["SQL, Python 활용 경험", "대시보드 또는 리포트 제작 경험"],
             preferred=["Airflow, dbt 사용 경험"])
    marketer = job("그로스 마케터", requirements=["SQL로 지표를 조회해 본 경험"])
    assert review(fde)["relevant"] and review(backend)["relevant"] and review(bi)["relevant"]
    assert not review(marketer)["relevant"]
    assert {"SQL", "Airflow", "dbt"} <= set(review(bi)["data_matches"])


def test_adjacent_ai_and_backend_roles_are_kept_and_labelled():
    ai = job("AI Engineer", duties=["LLM 기반 RAG 서비스 개발"], requirements=["Python 활용 능력"])
    backend = job("Server Developer", duties=["Spring 기반 API 개발"], requirements=["AWS 사용 경험"])
    pipeline = job("Data Engineer", duties=["Airflow 파이프라인 운영", "SQL 모델링"])
    designer = job("모션그래픽 디자이너", duties=["3D/AI영상 제작"])
    assert review(ai)["category"] == "인접" and review(ai)["relevant"]
    assert review(backend)["category"] == "인접" and review(backend)["relevant"]
    assert review(pipeline)["category"] == "데이터"
    assert not review(designer)["relevant"]


def test_three_or_more_required_years_are_excluded():
    base = dict(duties=["데이터 파이프라인 개발", "Spark 배치 처리"])
    assert min_years_required(job("DE", requirements=["데이터 엔지니어링 경력 3년 이상"])) == 3
    assert min_years_required(job("DE", requirements=["관련 경력 3~5년"])) == 3
    assert min_years_required(job("DE", requirements=["5+ years of experience with SQL"])) == 5
    assert min_years_required(job("DE", requirements=["경력 1~3년"])) == 1
    assert min_years_required(job("DE", requirements=["신입 또는 경력 무관"])) is None
    assert not review(job("DE", requirements=["경력 3년 이상"], **base))["relevant"]
    assert review(job("DE", requirements=["경력 1~3년"], **base))["relevant"]
    # Years mentioned only as a preference do not exclude the posting.
    assert review(job("DE", preferred=["5년 이상 경력"], **base))["relevant"]


def test_digest_is_sent_once_per_posting():
    item = {"title": "Data Engineer", "url": "https://example.org/1", "deadline_date": None,
            "data_matches": ["SQL", "Airflow"]}
    session = Mock()
    session.post.return_value = Mock(status_code=200, text="ok")
    webhook = "https://hooks.slack.com/services/test/test/test"
    with closing(sqlite3.connect(":memory:")) as db:
        assert notify_new_postings(db, session, webhook, [item]) == {"sent": 1}
        assert notify_new_postings(db, session, webhook, [item]) == {"sent": 0}
    text = session.post.call_args.kwargs["json"]["text"]
    assert "채용 시 마감" in text and "SQL, Airflow" in text and "[데이터]" in text
    assert session.post.call_count == 1
