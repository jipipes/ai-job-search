"""Notion Job Tracker property mapping and Slack deadline reminders."""

from dataclasses import dataclass
from datetime import date, datetime
import os
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import requests

NOTION_API = "https://api.notion.com/v1"
SEOUL = ZoneInfo("Asia/Seoul")
REMINDER_DAYS = {7, 5, 3, 1, 0}


@dataclass(frozen=True)
class TrackerFields:
    title: str = "공고명"
    company: str = "회사"
    url: str = "공고 URL"
    stage: str = "지원 단계"
    deadline: str = "마감일"
    planned: str = "지원 예정"
    stage_type: str = "select"
    applied: str = ""

    @classmethod
    def from_environment(cls):
        defaults = cls()
        values = {
            key: os.environ.get(f"JOB_TRACKER_{key.upper()}", value)
            for key, value in vars(defaults).items()
        }
        if not values["applied"]:
            values["applied"] = os.environ.get("JOB_TRACKER_APPLIED_DATE", "")
        fields = cls(**values)
        if fields.stage_type not in {"select", "status"}:
            raise ValueError("JOB_TRACKER_STAGE_TYPE must be select or status")
        return fields


def has_value(prop):
    """True when a Notion property value is filled in (date, select, checkbox, text...)."""
    if "type" in prop:
        return bool(prop.get(prop["type"]))
    return any(prop.values())


def notion_query(session, data_source, payload):
    response = session.post(f"{NOTION_API}/data_sources/{data_source}/query",
                            json=payload, timeout=20)
    response.raise_for_status()
    return response.json()


def tracker_schema(session, data_source):
    response = session.get(f"{NOTION_API}/data_sources/{data_source}", timeout=20)
    response.raise_for_status()
    return {
        name: {"type": prop["type"], "options": [
            option["name"] for option in prop[prop["type"]].get("options", [])
        ]}
        for name, prop in response.json()["properties"].items()
    }


def planned_pages(session, data_source, fields):
    payload = {"filter": {
        "property": fields.stage,
        fields.stage_type: {"equals": fields.planned},
    }}
    while True:
        result = notion_query(session, data_source, payload)
        yield from result["results"]
        if not result.get("has_more"):
            return
        payload["start_cursor"] = result["next_cursor"]


def deadline_day(value):
    if "T" not in value:
        return date.fromisoformat(value)
    timestamp = datetime.fromisoformat(value)
    return timestamp.astimezone(SEOUL).date() if timestamp.tzinfo else timestamp.date()


def due_reminders(pages, fields, today):
    for page in pages:
        properties = page["properties"]
        if page.get("archived") or page.get("in_trash"):
            continue
        stage = properties[fields.stage].get(fields.stage_type) or {}
        if stage.get("name") != fields.planned:
            continue
        if fields.applied and has_value(properties[fields.applied]):
            continue
        deadline = properties[fields.deadline]["date"]
        if not deadline or not deadline.get("start"):
            continue
        day = deadline_day(deadline["start"])
        days_left = (day - today).days
        if days_left in REMINDER_DAYS:
            title = "".join(part["plain_text"] for part in properties[fields.title]["title"])
            yield {"id": page["id"], "title": title, "deadline": day.isoformat(),
                   "days_left": days_left, "notion_url": page["url"]}


def check_webhook(webhook, name="SLACK_WEBHOOK_URL"):
    parts = urlsplit(webhook)
    if (parts.scheme != "https" or parts.netloc not in {"hooks.slack.com", "hooks.slack-gov.com"}
            or not parts.path.startswith("/services/")):
        raise ValueError(f"{name} must be a Slack incoming webhook URL")


def send_reminders(db, session, webhook, reminders):
    check_webhook(webhook)
    db.execute("""CREATE TABLE IF NOT EXISTS deadline_reminders (
        page_id TEXT NOT NULL, deadline TEXT NOT NULL, days_left INTEGER NOT NULL,
        PRIMARY KEY (page_id, deadline, days_left)
    )""")
    sent = []
    errors = []
    for reminder in reminders:
        key = (reminder["id"], reminder["deadline"], reminder["days_left"])
        with db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute(
                "SELECT 1 FROM deadline_reminders WHERE page_id=? AND deadline=? AND days_left=?", key
            ).fetchone():
                continue
            days = reminder["days_left"]
            label = f"D-{days}" if days else "오늘 마감"
            try:
                response = session.post(webhook, timeout=15, allow_redirects=False, json={
                    "text": f"[{label}] {reminder['title']} · {reminder['deadline']}\n{reminder['notion_url']}",
                    "mrkdwn": False,
                    "unfurl_links": False,
                })
            except requests.RequestException:
                errors.append({"id": reminder["id"], "error": "Slack request failed"})
                continue
            if response.status_code != 200 or response.text.strip() != "ok":
                errors.append({"id": reminder["id"], "error": f"Slack HTTP {response.status_code}"})
                continue
            db.execute("INSERT INTO deadline_reminders VALUES (?, ?, ?)", key)
            sent.append(reminder)
    return {"sent": sent, "errors": errors}
