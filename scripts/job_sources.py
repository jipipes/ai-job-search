"""Additional Korean job sources: Jumpit (점핏) and the Saramin Open API.

Each source yields ``(url, version, job)`` where ``version`` changes when the listing
changes and ``job`` has the same shape as a The Founders posting, so storage,
filtering, Notion tracking and Slack digests treat every source alike.
"""

from datetime import date, datetime
import hashlib
import html
import json
import re
import time
from xml.etree import ElementTree

from scripts.job_tracker_notion import SEOUL

USER_AGENT = "job-digest/0.1 (personal daily job digest; low volume)"

JUMPIT_API = "https://jumpit-api.saramin.co.kr/api"
JUMPIT_WEB = "https://jumpit.saramin.co.kr/position/"
# 1 서버/백엔드, 7 DBA, 8 인공지능/머신러닝, 9 devops/시스템, 19 빅데이터 엔지니어, 20 AI 관련
JUMPIT_CATEGORIES = (19, 8, 20, 1, 7, 9)
JUMPIT_MAX_PAGES = 15
JUMPIT_DAILY_DETAILS = 60  # detail requests per run, one second apart

SARAMIN_API = "https://oapi.saramin.co.kr/job-search"
SARAMIN_JOB_MID = "2"  # IT개발·데이터
SARAMIN_PAGE_SIZE = 110
SARAMIN_MAX_PAGES = 5


def _lines(text):
    if not text:
        return None
    lines = [line.strip(" •-·*\t") for line in str(text).splitlines()]
    return [line for line in lines if line] or None


def _version(*values):
    return hashlib.sha1(json.dumps(values, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


def _get_json(session, url, **kwargs):
    response = session.get(url, timeout=20, headers={"User-Agent": USER_AGENT,
                                                     "Accept": "application/json"}, **kwargs)
    response.raise_for_status()
    return response.json()


# --- Jumpit -------------------------------------------------------------------------

def jumpit_listings(session, pause=0.5):
    """All listings in the configured categories, de-duplicated by id."""
    seen = {}
    for category in JUMPIT_CATEGORIES:
        for page in range(1, JUMPIT_MAX_PAGES + 1):
            result = _get_json(session, f"{JUMPIT_API}/positions",
                               params={"sort": "reg_dt", "page": page, "jobCategory": category})["result"]
            positions = result.get("positions") or []
            for item in positions:
                seen.setdefault(item["id"], item)
            time.sleep(pause)
            if not positions or page * len(positions) >= result.get("totalCount", 0):
                break
    return list(seen.values())


def jumpit_version(item):
    return _version(item.get("title"), item.get("closedAt"), item.get("minCareer"),
                    item.get("techStacks"), item.get("alwaysOpen"))


def jumpit_job(detail, item):
    closed = detail.get("closedAt") or item.get("closedAt")
    always_open = detail.get("alwaysOpen", item.get("alwaysOpen"))
    deadline = None if always_open or not closed else closed[:10]
    min_career = detail.get("minCareer", item.get("minCareer"))
    stacks = detail.get("techStacks") or item.get("techStacks") or []
    stacks = [s.get("stack", s) if isinstance(s, dict) else s for s in stacks]
    return {
        "url": JUMPIT_WEB + str(item["id"]),
        "source": "jumpit",
        "company": detail.get("companyName") or item.get("companyName"),
        "title": detail.get("title") or item.get("title"),
        "entry_level_eligible": bool(detail.get("newcomer", item.get("newcomer"))) or min_career == 0,
        "min_career": min_career,
        "employment_type": None,
        "source_labels": [item.get("jobCategory"), *stacks],
        "deadline_type": "until_filled" if always_open else ("fixed" if deadline else "unknown"),
        "deadline_date": deadline,
        "duties": _lines(detail.get("responsibility")),
        "requirements": _lines(detail.get("qualifications")),
        "preferred_requirements": _lines(detail.get("preferredRequirements")),
        "tech_stacks": stacks,
        "work_conditions": None,
        "application_questions": None,
        "application_instructions": _lines(detail.get("recruitProcess")),
    }


def jumpit_entries(session, max_years):
    """Listings worth a detail fetch: below the experience cut, as (url, version, item)."""
    for item in jumpit_listings(session):
        if (item.get("minCareer") or 0) >= max_years and not item.get("newcomer"):
            continue
        yield JUMPIT_WEB + str(item["id"]), jumpit_version(item), item


def jumpit_fetch(session, item):
    detail = _get_json(session, f"{JUMPIT_API}/position/{item['id']}")["result"]
    return jumpit_job(detail, item)


# --- Saramin Open API ----------------------------------------------------------------

def _saramin_time(value):
    if not value:
        return None
    return datetime.fromtimestamp(int(value), SEOUL).date().isoformat()


def saramin_job(item):
    position = item.get("position") or {}
    experience = position.get("experience-level") or {}
    close_type = (item.get("close-type") or {}).get("code")
    deadline = _saramin_time(item.get("expiration-timestamp"))
    if close_type in ("2", "3", "4"):  # 채용시/상시/수시
        deadline = None
    job_codes = (position.get("job-code") or {}).get("name") or ""
    keywords = item.get("keyword") or ""
    min_years = experience.get("min")
    requirement = []
    if experience.get("name"):
        requirement.append(f"경력: {experience['name']}")
    if isinstance(min_years, int) and min_years > 0:
        requirement.append(f"경력 {min_years}년 이상")
    return {
        "url": item.get("url"),
        "source": "saramin",
        "company": ((item.get("company") or {}).get("detail") or {}).get("name"),
        "title": position.get("title"),
        "entry_level_eligible": experience.get("code") in (1, 3, 0, "1", "3", "0"),
        "min_career": min_years,
        "employment_type": (position.get("job-type") or {}).get("name"),
        "source_labels": [part for part in (job_codes, keywords) if part],
        "deadline_type": "fixed" if deadline else "until_filled",
        "deadline_date": deadline,
        # The API gives no body text; job codes and keywords stand in for duties.
        "duties": [part for part in (job_codes.replace(",", ", "), keywords) if part] or None,
        "requirements": requirement or None,
        "preferred_requirements": None,
        "work_conditions": None,
        "application_questions": None,
        "application_instructions": None,
    }


def saramin_entries(session, access_key, max_years, pause=0.5):
    for page in range(SARAMIN_MAX_PAGES):
        data = _get_json(session, SARAMIN_API, params={
            "access-key": access_key, "job_mid_cd": SARAMIN_JOB_MID, "sr": "directhire",
            "fields": "posting-date,expiration-date,keyword-code", "sort": "pd",
            "start": page, "count": SARAMIN_PAGE_SIZE,
        })
        jobs = (data.get("jobs") or {}).get("job") or []
        for item in jobs:
            job = saramin_job(item)
            if not job["url"] or (job["min_career"] or 0) >= max_years:
                continue
            yield job["url"], _version(item.get("modification-timestamp"), job["title"],
                                       job["deadline_date"]), job
        total = int((data.get("jobs") or {}).get("total") or 0)
        if not jobs or (page + 1) * SARAMIN_PAGE_SIZE >= total:
            break
        time.sleep(pause)


# --- Linkareer (링커리어) --------------------------------------------------------------

LINKAREER_WEB = "https://linkareer.com"
LINKAREER_CATEGORY = "100003"  # IT/개발 (AI/ML, DevOps, QA 포함)
LINKAREER_MAX_PAGES = 30
LINKAREER_DAILY_DETAILS = 60
_NEXT_DATA = re.compile(r"<script id=\"__NEXT_DATA__\"[^>]*>(.*?)</script>", re.S)
_SECTION_KEYS = (
    ("preferred_requirements", re.compile(r"우대")),
    ("requirements", re.compile(r"자격|지원\s*조건|필수|요건|requirement|qualification", re.I)),
    ("duties", re.compile(r"업무|담당|직무|하는\s*일|모집\s*분야|responsib|role", re.I)),
)


def _apollo(page_html):
    match = _NEXT_DATA.search(page_html)
    if not match:
        raise ValueError("Linkareer page has no __NEXT_DATA__")
    return json.loads(match.group(1))["props"]["pageProps"]["__APOLLO_STATE__"]


def _get_text(session, url, **kwargs):
    response = session.get(url, timeout=20, headers={"User-Agent": USER_AGENT}, **kwargs)
    response.raise_for_status()
    return response.text


def split_sections(text):
    """Split a free-form posting body into duties / requirements / preferred lines."""
    sections = {"duties": [], "requirements": [], "preferred_requirements": []}
    current = "duties"
    for raw in text.splitlines():
        line = raw.strip(" •-·*▪■□○●◆◇※\t")
        if not line:
            continue
        bracketed = re.fullmatch(r"[\[【<]\s*([^\]】>]{1,20})\s*[\]】>]\s*:?", line)
        label = bracketed.group(1) if bracketed else (line.rstrip(":") if len(line) <= 12 else None)
        key = next((key for key, pattern in _SECTION_KEYS if label and pattern.search(label)), None)
        if key:
            current = key
            continue
        if bracketed:  # some other heading such as [채용절차]: stop collecting
            current = "other"
            continue
        sections.setdefault(current, []).append(line)
    sections.pop("other", None)
    return {key: value or None for key, value in sections.items()}


def _millis_date(value):
    if not value:
        return None
    return datetime.fromtimestamp(int(value) / 1000, SEOUL).date().isoformat()


def linkareer_entries(session, pause=1.0):
    seen = set()
    for page in range(1, LINKAREER_MAX_PAGES + 1):
        state = _apollo(_get_text(session, f"{LINKAREER_WEB}/list/recruit", params={
            "filterBy_categoryIDs": LINKAREER_CATEGORY, "orderBy_direction": "DESC",
            "orderBy_field": "RECENT", "page": page}))
        query = next((value for key, value in state["ROOT_QUERY"].items()
                      if key.startswith("activities(")), {})
        nodes = [state[ref["__ref"]] for ref in query.get("nodes", []) if ref.get("__ref") in state]
        for item in nodes:
            if item["id"] in seen:
                continue
            seen.add(item["id"])
            yield (f"{LINKAREER_WEB}/activity/{item['id']}",
                   _version(item.get("title"), item.get("recruitCloseAt"), item.get("jobTypes")), item)
        if not nodes or page * 20 >= query.get("totalCount", 0):
            break
        time.sleep(pause)


def linkareer_job(activity, body_html, item=None):
    item = item or {}
    text = html.unescape(re.sub(r"<br\s*/?>|</p>|</li>|</div>|</h\d>", "\n", body_html or "", flags=re.I))
    text = re.sub(r"<[^>]+>", "", text)
    sections = split_sections(text)
    job_types = activity.get("jobTypes") or item.get("jobTypes") or []
    close = activity.get("recruitCloseAt") or item.get("recruitCloseAt")
    return {
        "url": f"{LINKAREER_WEB}/activity/{activity['id']}",
        "source": "linkareer",
        "company": activity.get("organizationName") or item.get("organizationName"),
        "title": activity.get("title") or item.get("title"),
        "entry_level_eligible": any(kind in job_types for kind in ("NEW", "INTERN")) or None,
        "employment_type": ", ".join(job_types) or None,
        "source_labels": job_types,
        "deadline_type": "fixed" if close else "until_filled",
        "deadline_date": _millis_date(close),
        **sections,
        "work_conditions": None,
        "application_questions": None,
        "application_instructions": None,
    }


def linkareer_fetch(session, item):
    state = _apollo(_get_text(session, f"{LINKAREER_WEB}/activity/{item['id']}"))
    activity = next(value for key, value in state.items()
                    if key.startswith("Activity:") and value.get("id") == str(item["id"]))
    ref = (activity.get("detailText") or {}).get("__ref")
    body = (state.get(ref) or {}).get("text", "") if ref else ""
    return linkareer_job(activity, body, item)


# --- 고용24 (워크넷) Open API ----------------------------------------------------------

WORK24_API = "https://www.work24.go.kr/cm/openApi/call/wk/callOpenApiSvcInfo210L01.do"
WORK24_KEYWORDS = ("데이터 엔지니어", "빅데이터", "데이터 분석", "AI 엔지니어", "머신러닝",
                   "백엔드 개발", "데이터베이스")
WORK24_PAGE_SIZE = 100


def _work24_date(value):
    match = re.search(r"(\d{2,4})-(\d{1,2})-(\d{1,2})", value or "")
    if not match:
        return None
    year, month, day = (int(part) for part in match.groups())
    return date(year + 2000 if year < 100 else year, month, day).isoformat()


def work24_job(node, keyword=""):
    get = lambda tag: (node.findtext(tag) or "").strip()
    close = get("closeDt")
    deadline = None if "채용시" in close else _work24_date(close)
    career = get("career")
    return {
        "url": get("wantedInfoUrl"),
        "source": "work24",
        "company": get("company"),
        "title": get("title"),
        "entry_level_eligible": ("신입" in career or "관계없음" in career) or None,
        "employment_type": None,
        "source_labels": [part for part in (get("indTpNm"), get("region"), keyword) if part],
        "deadline_type": "fixed" if deadline else "until_filled",
        "deadline_date": deadline,
        # The list API has no body; the search keyword and industry stand in for duties.
        "duties": [part for part in (keyword, get("indTpNm")) if part] or None,
        "requirements": [f"경력: {career}"] if career else None,
        "preferred_requirements": None,
        "work_conditions": [part for part in (get("sal"), get("holidayTpNm")) if part] or None,
        "application_questions": None,
        "application_instructions": None,
    }


def work24_entries(session, auth_key, pause=0.5):
    seen = set()
    for keyword in WORK24_KEYWORDS:
        response = session.get(WORK24_API, timeout=30, headers={"User-Agent": USER_AGENT}, params={
            "authKey": auth_key, "callTp": "L", "returnType": "XML", "startPage": 1,
            "display": WORK24_PAGE_SIZE, "keyword": keyword, "regDate": "M-1", "empTpGb": "1",
        })
        response.raise_for_status()
        root = ElementTree.fromstring(response.content)
        error = root.findtext("message") or root.findtext("messageCd")
        if root.tag != "wantedRoot" and error:
            raise ValueError(f"고용24 API: {error}")
        for node in root.iter("wanted"):
            job = work24_job(node, keyword)
            if not job["url"] or job["url"] in seen:
                continue
            seen.add(job["url"])
            yield job["url"], _version(node.findtext("smodifyDtm"), job["title"], job["deadline_date"]), job
        time.sleep(pause)
