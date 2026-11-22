"""Collect a daily job digest and send explicitly selected postings to Notion."""

import argparse
from datetime import datetime, timedelta
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
from urllib.robotparser import RobotFileParser
from xml.etree import ElementTree

import requests

from scripts import job_sources as sources
from scripts import posting_filter
from scripts import thefounders_jobs as founders
from scripts.job_tracker_notion import (
    NOTION_API, SEOUL, TrackerFields, due_reminders, planned_pages,
    send_reminders, tracker_schema,
)



def collect(db, session, limit, now):
    session.headers["User-Agent"] = founders.USER_AGENT
    robots = RobotFileParser()
    robots.parse(founders.fetch(session, founders.BASE + "/robots.txt").decode().splitlines())
    if not robots.can_fetch(founders.USER_AGENT, founders.SITEMAP):
        raise ValueError("Sitemap access is disallowed by robots.txt")
    entries = founders.discover_entries(founders.fetch(session, founders.SITEMAP))
    existing = _existing(db)
    due = []
    for url, lastmod in entries.items():
        previous = existing.get(url)
        if previous is None or lastmod != previous[0]:
            due.append(url)
        elif datetime.fromisoformat(previous[1]) <= now - timedelta(days=7):
            due.append(url)
    changes = []
    errors = []
    for index, url in enumerate(due[:limit]):
        if index:
            time.sleep(1)
        try:
            if not robots.can_fetch(founders.USER_AGENT, url):
                raise ValueError("Disallowed by robots.txt")
            job = founders.parse_posting(founders.fetch(session, url), url)
        except (requests.RequestException, ValueError) as error:
            errors.append({"url": url, "error": str(error)})
            continue
        store(db, existing, url, entries[url], job, now, changes)
    return {"checked_at": now.isoformat(), "discovered": len(entries),
            "remaining": max(0, len(due) - limit) + len(errors),
            "changes": changes, "errors": errors}


def _existing(db):
    return {
        url: (lastmod, checked_at, payload)
        for url, lastmod, checked_at, payload in db.execute(
            "SELECT url, lastmod, checked_at, payload FROM postings"
        )
    }


def store(db, existing, url, version, job, now, changes):
    """Save a posting and record it in ``changes`` when it is new or its content changed."""
    payload = json.dumps(job, ensure_ascii=False, sort_keys=True)
    previous = existing.get(url)
    if previous is None or previous[2] != payload:
        changes.append({
            "change": "new" if previous is None else "updated",
            "source": job.get("source", "thefounders"),
            "id": url.rstrip("/").rsplit("/", 1)[-1],
            "company": job.get("company"),
            "title": job["title"],
            "url": url,
            "entry_level_eligible": job["entry_level_eligible"],
            "deadline_date": job.get("deadline_date"),
            **posting_filter.review(job),
        })
    db.execute(
        """INSERT INTO postings (url, lastmod, checked_at, payload) VALUES (?, ?, ?, ?)
           ON CONFLICT(url) DO UPDATE SET
           lastmod=excluded.lastmod, checked_at=excluded.checked_at, payload=excluded.payload""",
        (url, version, now.isoformat(), payload),
    )


def collect_source(db, entries, now, limit, fetch=None, pause=1.0):
    """Store postings from ``entries`` (url, version, item) that are new, changed or stale.

    ``fetch(item)`` turns a listing into a full job (one request each, capped by
    ``limit``); without it the item already is the job.
    """
    existing = _existing(db)
    entries = list(entries)
    due = [
        (url, version, item) for url, version, item in entries
        if url not in existing or existing[url][0] != version
        or datetime.fromisoformat(existing[url][1]) <= now - timedelta(days=7)
    ]
    batch = due[:limit] if fetch else due
    changes, errors = [], []
    for index, (url, version, item) in enumerate(batch):
        try:
            if fetch:
                if index:
                    time.sleep(pause)
                job = fetch(item)
            else:
                job = item
        except (requests.RequestException, ValueError, KeyError) as error:
            errors.append({"url": url, "error": str(error)})
            continue
        store(db, existing, url, version, job, now, changes)
    return {"discovered": len(entries), "remaining": len(due) - len(batch) + len(errors),
            "changes": changes, "errors": errors}


def run_daily(db, session, limit, now):
    """Run every configured source; one failing source does not stop the others."""
    result = {"checked_at": now.isoformat(), "sources": {}, "changes": [], "errors": []}
    max_years = posting_filter.MAX_YEARS
    runs = [("thefounders", lambda: collect(db, session, limit, now))]
    runs.append(("jumpit", lambda: collect_source(
        db, sources.jumpit_entries(session, max_years), now, max(limit, sources.JUMPIT_DAILY_DETAILS),
        fetch=lambda item: sources.jumpit_fetch(session, item))))
    key = os.environ.get("SARAMIN_ACCESS_KEY")
    if key:
        runs.append(("saramin", lambda: collect_source(
            db, sources.saramin_entries(session, key, max_years), now, limit)))
    for name, run in runs:
        try:
            outcome = run()
        except (requests.RequestException, ValueError, KeyError, ElementTree.ParseError) as error:
            result["errors"].append({"source": name, "error": str(error)})
            continue
        result["sources"][name] = {"discovered": outcome["discovered"],
                                   "remaining": outcome["remaining"],
                                   "changes": len(outcome["changes"])}
        result["changes"] += outcome["changes"]
        result["errors"] += [{"source": name, **error} for error in outcome["errors"]]
    return result


def register_notion(session, data_source, job, fields=TrackerFields()):
    query = session.post(
        f"{NOTION_API}/data_sources/{data_source}/query",
        json={"filter": {"property": fields.url, "url": {"equals": job["url"]}}, "page_size": 1},
        timeout=20,
    )
    query.raise_for_status()
    matches = query.json()["results"]
    if matches:
        return {"created": False, "notion_url": matches[0]["url"]}

    title = job["title"]
    if not fields.company and job["company"]:
        title = f"{job['company']} / {title}"
    properties = {
        fields.title: {"title": [{"text": {"content": title}}]},
        fields.url: {"url": job["url"]},
        fields.stage: {fields.stage_type: {"name": fields.planned}},
    }
    if fields.company:
        properties[fields.company] = {"rich_text": [{"text": {"content": job["company"]}}]}
    if job["deadline_date"]:
        properties[fields.deadline] = {"date": {"start": job["deadline_date"]}}
    response = session.post(
        f"{NOTION_API}/pages",
        json={"parent": {"type": "data_source_id", "data_source_id": data_source},
              "properties": properties},
        timeout=20,
    )
    response.raise_for_status()
    return {"created": True, "notion_url": response.json()["url"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("results/korean_de_jobs.sqlite3"))
    commands = parser.add_subparsers(dest="command", required=True)
    daily = commands.add_parser("daily", help="Discover and report new or updated jobs")
    daily.add_argument("--limit", type=int, default=15)
    commands.add_parser("jobs", help="List locally collected jobs")
    track = commands.add_parser("track", help="Register a selected job in Notion as 지원 예정")
    track.add_argument("posting", help="Official posting URL or posting ID")
    commands.add_parser("notion-schema", help="Show the existing Job Tracker properties")
    remind = commands.add_parser("remind", help="Check Notion and send due Slack reminders")
    remind.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.command == "daily" and args.limit < 1:
        parser.error("--limit must be positive")

    try:
        args.db.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(args.db, timeout=60)) as db, requests.Session() as session, db:
            db.execute("""CREATE TABLE IF NOT EXISTS postings (
                url TEXT PRIMARY KEY, lastmod TEXT NOT NULL,
                checked_at TEXT NOT NULL, payload TEXT NOT NULL
            )""")
            if args.command == "daily":
                db.execute("BEGIN IMMEDIATE")
                result = run_daily(db, session, args.limit, datetime.now(SEOUL))
                webhook = os.environ.get("SLACK_NEW_POSTINGS_WEBHOOK_URL")
                relevant = [item for item in result["changes"] if item["relevant"]]
                if webhook and relevant:
                    with requests.Session() as slack:
                        notice = posting_filter.notify_new_postings(db, slack, webhook, relevant)
                    result["slack"] = notice
                    if notice.get("error"):
                        result["errors"].append({"slack": notice["error"]})
            elif args.command == "jobs":
                result = [
                    json.loads(payload) for (payload,) in db.execute(
                        "SELECT payload FROM postings ORDER BY lastmod DESC, url"
                    )
                ]
            else:
                token = os.environ.get("NOTION_TOKEN")
                data_source = os.environ.get("NOTION_DATA_SOURCE_ID")
                if not token or not data_source:
                    raise ValueError("Set NOTION_TOKEN and NOTION_DATA_SOURCE_ID")
                session.headers.update({
                    "Authorization": f"Bearer {token}",
                    "Notion-Version": "2025-09-03",
                })
                fields = TrackerFields.from_environment()
                if args.command == "notion-schema":
                    result = tracker_schema(session, data_source)
                elif args.command == "remind":
                    pages = planned_pages(session, data_source, fields)
                    reminders = list(due_reminders(pages, fields, datetime.now(SEOUL).date()))
                    if args.dry_run:
                        result = {"due": reminders}
                    else:
                        webhook = os.environ.get("SLACK_WEBHOOK_URL")
                        if not webhook:
                            raise ValueError("Set SLACK_WEBHOOK_URL")
                        with requests.Session() as slack:
                            result = send_reminders(db, slack, webhook, reminders)
                else:
                    db.execute("BEGIN IMMEDIATE")
                    row = None
                    for url in (args.posting, founders.posting_url(args.posting),
                                founders.posting_url(f"{founders.BASE}/job_posting/{args.posting}"),
                                sources.JUMPIT_WEB + args.posting):
                        if url:
                            row = db.execute("SELECT payload FROM postings WHERE url=?",
                                             (url,)).fetchone()
                        if row:
                            break
                    if row is None:
                        raise ValueError("Posting is absent from the local cache. Run daily first.")
                    result = register_notion(session, data_source, json.loads(row[0]), fields)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return int(isinstance(result, dict) and bool(result.get("errors")))
    except (OSError, ValueError, sqlite3.Error, requests.RequestException, ElementTree.ParseError) as error:
        print(error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
