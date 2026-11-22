from contextlib import closing
from datetime import datetime
import sqlite3
from unittest.mock import Mock

from scripts import korean_de_jobs as jobs


def test_daily_reports_new_unchanged_and_updated(monkeypatch):
    url = jobs.founders.BASE + "/job_posting/example"
    lastmod = "2026-11-19"
    title = "Data Engineer"

    def fetch(session, address):
        if address == jobs.founders.SITEMAP:
            return f"<urlset><url><loc>{url}</loc><lastmod>{lastmod}</lastmod></url></urlset>".encode()
        if address == url:
            return title.encode()
        return b"User-agent: *\nDisallow: /api/"

    monkeypatch.setattr(jobs.founders, "fetch", fetch)
    monkeypatch.setattr(jobs.founders, "parse_posting",
                        lambda html, url: {"title": html.decode(), "entry_level_eligible": True})
    now = datetime(2026, 11, 22, tzinfo=jobs.SEOUL)
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute("CREATE TABLE postings (url PRIMARY KEY, lastmod, checked_at, payload)")
        assert jobs.collect(db, Mock(headers={}), 5, now)["changes"][0]["change"] == "new"
        assert jobs.collect(db, Mock(headers={}), 5, now)["changes"] == []
        lastmod = "2026-11-20"
        title = "BI & Data Engineer"
        assert jobs.collect(db, Mock(headers={}), 5, now)["changes"][0]["change"] == "updated"


def test_notion_reuses_existing_page_and_creates_with_planned_status():
    session = Mock()
    query = Mock()
    create = Mock()
    query.json.return_value = {"results": []}
    create.json.return_value = {"url": "https://www.notion.so/example"}
    session.post.side_effect = [query, create]
    job = {"url": "https://example.org/job", "title": "Data Engineer",
           "company": "Example", "deadline_date": None}
    assert jobs.register_notion(session, "source", job)["created"]
    properties = session.post.call_args.kwargs["json"]["properties"]
    assert properties["지원 단계"] == {"select": {"name": "지원 예정"}}
    assert "마감일" not in properties

    query.json.return_value = {"results": [{"url": create.json.return_value["url"]}]}
    session.reset_mock()
    session.post.side_effect = [query]
    assert not jobs.register_notion(session, "source", job)["created"]
    assert session.post.call_count == 1
