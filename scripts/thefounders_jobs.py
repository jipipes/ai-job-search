"""Discover and normalize public postings from The Founders careers site."""

import argparse
from datetime import date
import json
import re
import sys
import time
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser
from xml.etree import ElementTree

from bs4 import BeautifulSoup
import requests

BASE = "https://thefounders.careers"
SITEMAP = BASE + "/sitemap.xml"
USER_AGENT = "Korean-DE-Job-Agent"
POSTING_PATH = re.compile(r"/job_posting/([A-Za-z0-9_-]+)/?")
SECTION_NAMES = {
    "주요업무": "duties",
    "자격요건": "requirements",
    "우대사항": "preferred_requirements",
    "지원방법": "application",
    "근무조건및환경": "work_conditions",
}


def posting_url(url):
    parts = urlsplit(url.strip())
    match = POSTING_PATH.fullmatch(parts.path)
    if parts.scheme == "https" and parts.netloc == "thefounders.careers" and match:
        return f"{BASE}/job_posting/{match[1]}"
    return None


def discover_entries(xml):
    postings = {}
    for node in ElementTree.fromstring(xml).findall("{*}url"):
        url = posting_url(node.findtext("{*}loc", ""))
        if url:
            postings[url] = node.findtext("{*}lastmod", "")
    if not postings:
        raise ValueError("No posting URLs found in the careers sitemap")
    return dict(sorted(postings.items(), key=lambda entry: entry[1], reverse=True))


def discover(xml):
    return list(discover_entries(xml))


def parse_posting(html, url):
    soup = BeautifulSoup(html, "html.parser")
    header = soup.select_one('[class*="PostingLayout1Header__Header-"]')
    body = soup.select_one('[class*="PostingHTMLContent__Content-"]')
    title = header.find("h1") if header else None
    if title is None or body is None:
        raise ValueError("Posting title or body is missing")
    labels = [tag.get_text(" ", strip=True) for tag in
              header.select('[class*="LabelGroup__Label-"]')]
    labels = list(dict.fromkeys(labels))
    deadline_type, deadline_date = "unknown", None
    for label in labels:
        if re.fullmatch(r"채용\s*시\s*마감", label):
            deadline_type = "until_filled"
            break
        match = re.fullmatch(r"(\d{4})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{1,2})\.?\s*(?:마감)?", label)
        if match:
            deadline_date = date(*map(int, match.groups())).isoformat()
            deadline_type = "fixed"
            break

    for line_break in body.find_all("br"):
        line_break.replace_with("\n")

    sections = {}
    current = None
    for block in body.find_all(recursive=False):
        if block.name in ("h1", "h2"):
            heading = re.sub(r"[\sㅣ|]", "", block.get_text())
            current = SECTION_NAMES.get(heading)
        elif current:
            items = block.find_all("li") or [block]
            for item in items:
                # Keep each nested list item separate from its parent.
                for nested in item.find_all(["ul", "ol"], recursive=False):
                    nested.extract()
                for line in item.get_text().splitlines():
                    value = " ".join(line.split())
                    if value:
                        sections.setdefault(current, []).append(value)

    questions = []
    for value in sections.get("application", []):
        match = re.fullmatch(r"\d+\.\s*(.+)", value)
        if match:
            questions.append(match[1])
    return {
        "url": url,
        "company": "더파운더즈",
        "title": title.get_text(" ", strip=True),
        "entry_level_eligible": True if "신입" in labels else None,
        "employment_type": "인턴" if "인턴" in labels else None,
        "source_labels": labels,
        "deadline_type": deadline_type,
        "deadline_date": deadline_date,
        "duties": sections.get("duties"),
        "requirements": sections.get("requirements"),
        "preferred_requirements": sections.get("preferred_requirements"),
        "work_conditions": sections.get("work_conditions"),
        "application_questions": questions or None,
        "application_instructions": sections.get("application"),
    }


def fetch(session, url):
    response = session.get(url, timeout=20, allow_redirects=False)
    response.raise_for_status()
    if response.status_code != 200:
        raise ValueError(f"Unexpected HTTP status {response.status_code} for {url}")
    return response.content


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", help="Collect one official detail URL")
    parser.add_argument("--list", action="store_true", help="List all discovered posting URLs")
    parser.add_argument("--limit", type=int, default=5, help="Maximum detail requests in discovery mode")
    args = parser.parse_args()
    url = posting_url(args.url) if args.url else None
    if args.url and (url is None or args.list):
        parser.error("--url requires one official posting URL and cannot be combined with --list")
    if args.limit < 1:
        parser.error("--limit must be positive")

    with requests.Session() as session:
        session.headers["User-Agent"] = USER_AGENT
        robots = RobotFileParser()
        try:
            robots.parse(fetch(session, BASE + "/robots.txt").decode("utf-8").splitlines())
            if url:
                urls = [url]
            elif robots.can_fetch(USER_AGENT, SITEMAP):
                urls = discover(fetch(session, SITEMAP))
            else:
                raise ValueError("Sitemap access is disallowed by robots.txt")
            if args.list:
                print(json.dumps(urls, ensure_ascii=False, indent=2))
                return 0
        except (requests.RequestException, ValueError, ElementTree.ParseError) as error:
            print(error, file=sys.stderr)
            return 1

        postings = []
        errors = []
        for index, url in enumerate(urls[:args.limit]):
            if index:
                time.sleep(1)
            try:
                if not robots.can_fetch(USER_AGENT, url):
                    raise ValueError("Disallowed by robots.txt")
                postings.append(parse_posting(fetch(session, url), url))
            except (requests.RequestException, ValueError) as error:
                errors.append({"url": url, "error": str(error)})
        print(json.dumps({"postings": postings, "errors": errors}, ensure_ascii=False, indent=2))
        return int(bool(errors))


if __name__ == "__main__":
    sys.exit(main())
