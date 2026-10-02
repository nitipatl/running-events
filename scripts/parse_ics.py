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
    """Map explicit km figures to site buckets; None = non-standard (do not guess)."""
    for n in nums:
        if 41.0 <= n <= 43.0:
            return "42k"
    for n in nums:
        if 20.5 <= n <= 21.5:
            return "21.1k"
    for n in nums:
        if 9.5 <= n <= 10.5:
            return "10k"
    return None  # e.g. 11k / 15k / 30k — leave unset rather than mislabel


def detect_distance(text: str, title: str | None = None) -> str | None:
    """Prefer explicit km tokens, then half, then full marathon (not mini)."""
    # 1) Explicit km on title first (suffix like "- 10k"), then full text
    for source in ((title, text) if title else (text,)):
        if not source:
            continue
        nums = [float(m.group(1)) for m in _EXPLICIT_KM.finditer(source)]
        if nums:
            mapped = _map_explicit_kms(nums)
            # Had explicit km — never fall through to "marathon" heuristics
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

    # Replace the inline data block between sentinel comments
    # Use lambda to prevent re.sub from interpreting backslashes in data_js
    import re as _re
    replacement = f"// \nconst EVENTS_DATA={data_js};\n// "
    html = _re.sub(
        r"//.*?// ",
        lambda _: replacement,
        html,
        flags=_re.DOTALL,
    )

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Done. {total} events baked into index.html ({upcoming} upcoming, {past} past).")


if __name__ == "__main__":
    main()
