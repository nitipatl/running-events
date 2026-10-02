"""
Fetch public Google Calendar ICS, parse running events,
bake data directly into index.html (no runtime fetch needed).
"""

import json
import os
import re
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from urllib.request import urlopen
from icalendar import Calendar

BKK = ZoneInfo("Asia/Bangkok")

# Secret iCal URL (set as GitHub Secret: CALENDAR_ICS_URL)
# Secret URL includes attendee/guest data — public URL does not
ICS_URL = os.environ.get("CALENDAR_ICS_URL", "").strip()
if not ICS_URL:
    raise SystemExit(
        "ERROR: CALENDAR_ICS_URL secret is not set.\n"
        "Go to: Repo → Settings → Secrets → Actions → New secret\n"
        "Name: CALENDAR_ICS_URL\n"
        "Value: your secret iCal URL from Google Calendar settings"
    )

OUM_EMAIL = "my.jintawee@gmail.com"

# ── distance detection ─────────────────────────────────────────────────────────

_EXPLICIT_KM = re.compile(r"(\d+(?:\.\d+)?)\s*k(?:m)?\b", re.IGNORECASE)
_HALF = re.compile(r"half[\s\-]?marathon|ฮาล์ฟ|\bhalf\b", re.IGNORECASE)
_FULL = re.compile(r"full[\s\-]?marathon", re.IGNORECASE)
_MINI = re.compile(r"mini\s*marathon|มินิ\s*มาราธอน", re.IGNORECASE)
_MARATHON = re.compile(r"marathon|มาราธอน", re.IGNORECASE)
_TEN_KE = re.compile(r"เทนเค", re.IGNORECASE)


def _map_explicit_kms(nums: list[float]) -> str | None:
    """Snap any explicit km figure into filter buckets.

    Buckets: 5k / 10k / 21.1k / 42k

    Range rules (cover short + long):
    - n < 7.5              → 5k   (e.g. 4.5, 5, 6)
    - 7.5 <= n <= 15.5     → 10k  (e.g. 10, 11, 11.5, 12, 15)
    - 15.5 < n <= 21.1     → 21.1k (e.g. 21, 21.1)
    - 21.1 < n < 42        → 21.1k (round DOWN: 30, 33)
    - n >= 42              → 42k
    """
    n = nums[-1]
    if n < 7.5:
        return "5k"
    if n <= 15.5:
        return "10k"
    if n <= 21.1:
        return "21.1k"
    if n < 42.0:
        return "21.1k"  # over half, under full → round down to 21.1
    return "42k"


def detect_distance(text: str, title: str | None = None) -> str | None:
    """Prefer explicit km tokens, then half, then full marathon (not mini)."""
    # 1) Explicit km on title first (suffix like "- 10k"), then full text
    for source in ((title, text) if title else (text,)):
        if not source:
            continue
        nums = [float(m.group(1)) for m in _EXPLICIT_KM.finditer(source)]
        if not nums:
            # Trailing bare number: "Bangsaen 21" / "… — 11"
            # Ignore years / big ints (only plausible race km 1–50)
            m = re.search(r"(?<![\d.])(\d+(?:\.\d+)?)\s*$", source.strip())
            if m:
                v = float(m.group(1))
                if 1.0 <= v <= 50.0:
                    nums = [v]
        if nums:
            mapped = _map_explicit_kms(nums)
            # Had a numeric distance — never fall through to "marathon" heuristics
            return mapped

    combined = text or ""
    # 2) Half marathon / ฮาล์ฟ before bare marathon
    if _HALF.search(combined):
        return "21.1k"
    # 3) Full marathon keywords; mini marathon alone is not 42k
    if _FULL.search(combined):
        return "42k"
    if _MINI.search(combined):
        return None
    if _MARATHON.search(combined):
        return "42k"
    if _TEN_KE.search(combined):
        return "10k"
    return None


# ── participant detection ──────────────────────────────────────────────────────

def get_participants(component) -> list[str]:
    participants = ["Me"]
    attendees = component.get("ATTENDEE", [])
    if not isinstance(attendees, list):
        attendees = [attendees]
    for att in attendees:
        email = str(att).replace("mailto:", "").strip().lower()
        if OUM_EMAIL.lower() in email:
            participants.append("Oum")
            break
    return participants


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    now = datetime.now(BKK)
    range_start = now - timedelta(days=365)
    range_end = now + timedelta(days=365)

    print(f"Fetching ICS from Google Calendar …")
    with urlopen(ICS_URL, timeout=30) as resp:
        raw = resp.read()

    cal = Calendar.from_ical(raw)

    events = []
    for component in cal.walk():
        if component.name != "VEVENT":
            continue

        dtstart = component.get("DTSTART")
        if not dtstart:
            continue

        dt = dtstart.dt
        # icalendar returns date or datetime
        if hasattr(dt, "hour"):
            # datetime — convert to BKK timezone
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            dt_aware = dt.astimezone(BKK)
            time_str = dt_aware.strftime("%H:%M")
        else:
            # date-only — treat as midnight BKK (race hasn't started yet that day)
            dt_aware = datetime(dt.year, dt.month, dt.day, 0, 0, 0, tzinfo=BKK)
            time_str = None

        if not (range_start <= dt_aware <= range_end):
            continue

        summary = str(component.get("SUMMARY", "")).replace("\r\n", " ").replace("\n", " ").strip()
        description = str(component.get("DESCRIPTION", "")).replace("\r\n", " ").replace("\n", " ").strip()
        location = str(component.get("LOCATION", "")).replace("\r\n", " ").replace("\n", " ").strip()

        combined = f"{summary} {description}"
        distance = detect_distance(combined, title=summary)
        participants = get_participants(component)

        events.append({
            "title": summary,
            "date": dt_aware.strftime("%Y-%m-%d"),
            "time": time_str,
            "location": location,
            "distance": distance,
            "participants": participants,
            "isPast": dt_aware < now,
        })

    # chronological order
    events.sort(key=lambda e: e["date"])

    total = len(events)
    past = sum(1 for e in events if e["isPast"])
    upcoming = total - past

    # ── bake into index.html ───────────────────────────────────────────────────
    data_js = json.dumps(
        {"updated": now.isoformat(), "events": events},
        ensure_ascii=False,
        separators=(",", ":"),  # minified
    )

    with open("index.html", "r", encoding="utf-8") as f:
        html = f.read()


    # Replace only the marked data block (unique sentinels — do not match other // comments)
    import re as _re
    start, end = "<!--DATA_START-->", "<!--DATA_END-->"
    replacement = f"{start}
const EVENTS_DATA={data_js};
{end}"
    html, n = _re.subn(
        _re.escape(start) + r".*?" + _re.escape(end),
        lambda _: replacement,
        html,
        count=1,
        flags=_re.DOTALL,
    )
    if n != 1:
        raise SystemExit(f"ERROR: expected 1 DATA block in index.html, found {n}")

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Done. {total} events baked into index.html ({upcoming} upcoming, {past} past).")


if __name__ == "__main__":
    main()
