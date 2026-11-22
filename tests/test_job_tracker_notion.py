from contextlib import closing
from datetime import date, timedelta
import sqlite3
from unittest.mock import Mock

import requests

from scripts.job_tracker_notion import TrackerFields, due_reminders, send_reminders


def test_reminders_follow_current_notion_status_and_seoul_calendar():
    fields = TrackerFields(stage="Status", stage_type="status", planned="To apply",
                           applied_date="Applied")
    today = date(2026, 11, 23)

    def page(days, stage="To apply", applied=None, archived=False):
        deadline = None if days is None else {"start": (today + timedelta(days=days)).isoformat()}
        return {
            "id": str(days), "url": "https://www.notion.so/example", "archived": archived,
            "properties": {
                "Status": {"status": {"name": stage}},
                "Applied": {"date": applied},
                "마감일": {"date": deadline},
                "공고명": {"title": [{"plain_text": "Data Engineer"}]},
            },
        }

    pages = [page(days) for days in (7, 6, 5, 4, 3, 2, 1, 0, -1, None)]
    pages += [page(3, "Applied"), page(5, applied={"start": today.isoformat()}),
              page(7, archived=True)]
    midnight = page(1)
    midnight["properties"]["마감일"]["date"]["start"] = "2026-11-22T15:30:00Z"
    pages.append(midnight)
    assert [item["days_left"] for item in due_reminders(pages, fields, today)] == [7, 5, 3, 1, 0, 0]


def test_slack_retries_failed_notifications_and_skips_sent_ones():
    reminder = {"id": "job", "title": "BI Engineer", "deadline": "2026-11-30",
                "days_left": 7, "notion_url": "https://www.notion.so/example"}
    session = Mock()
    session.post.side_effect = [requests.Timeout(), Mock(status_code=200, text="ok")]
    webhook = "https://hooks.slack.com/services/test/test/test"
    with closing(sqlite3.connect(":memory:")) as db:
        assert send_reminders(db, session, webhook, [reminder])["errors"]
        assert send_reminders(db, session, webhook, [reminder])["sent"] == [reminder]
        assert send_reminders(db, session, webhook, [reminder])["sent"] == []
    assert session.post.call_count == 2
    assert "[D-7]" in session.post.call_args.kwargs["json"]["text"]
