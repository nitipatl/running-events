"""
Fetch public Google Calendar ICS, parse running events,
bake data directly into index.html (no runtime fetch needed).
"""

import json
import os
import re
import html as html_lib
from datetime import datetime, timezone, timedelta
from urllib.request import urlopen
from icalendar import Calendar

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

DISTANCE_PATTERNS = [
    # 42k / full marathon — check first (before half)
    ("42k",   re.compile(
        r'42[\s\.\-]?[12]?\s*k(m)?'
        r'|full[\s\-]?marathon'
        r'|marathon(?!.*half)'
        r'|มาราธอน(?!.*ฮาล์ฟ|.*half)',
        re.IGNORECASE
    )),
    # 21.1k / half marathon
    ("21.1k", re.compile(
        r'21[\s\.\-]?1?\s*k(m)?'
        r'|half[\s\-]?marathon'
        r'|ฮาล์ฟ'
        r'|half',
        re.IGNORECASE
    )),
    # 10k
    ("10k",   re.compile(
        r'10\s*k(m)?'
        r'|เทนเค',
        re.IGNORECASE
    )),
]


def detect_distance(text: str) -> str | None:
    for label, pattern in DISTANCE_PATTERNS:
        if pattern.search(text):
            return label
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
    now = datetime.now(timezone.utc)
    range_start = now - timedelta(days=365)
    range_end   = now + timedelta(days=365)

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
            # datetime — ensure timezone-aware
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            dt_aware = dt
            time_str = dt_aware.strftime("%H:%M")
        else:
            # date-only
            dt_aware = datetime(dt.year, dt.month, dt.day, 0, 0, 0, tzinfo=timezone.utc)
            time_str = None

        if not (range_start <= dt_aware <= range_end):
            continue

        summary     = str(component.get("SUMMARY",     ""))
        description = str(component.get("DESCRIPTION", ""))
        location    = str(component.get("LOCATION",    ""))

        combined  = f"{summary} {description}"
        distance  = detect_distance(combined)
        participants = get_participants(component)

        events.append({
            "title":        summary,
            "date":         dt_aware.strftime("%Y-%m-%d"),
            "time":         time_str,
            "location":     location,
            "distance":     distance,
            "participants": participants,
            "isPast":       dt_aware < now,
        })

    # chronological order
    events.sort(key=lambda e: e["date"])

    total    = len(events)
    past     = sum(1 for e in events if e["isPast"])
    upcoming = total - past

    # ── bake into index.html ───────────────────────────────────────────────────
    data_js = json.dumps(
        {"updated": now.isoformat(), "events": events},
        ensure_ascii=False,
        separators=(",", ":"),   # minified
    )

    with open("index.html", "r", encoding="utf-8") as f:
        html = f.read()

    # Replace the inline data block between sentinel comments
    import re as _re
    html = _re.sub(
        r"// <!--DATA_START-->.*?// <!--DATA_END-->",
        f"// <!--DATA_START-->\nconst EVENTS_DATA={data_js};\n// <!--DATA_END-->",
        html,
        flags=_re.DOTALL,
    )

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Done. {total} events baked into index.html ({upcoming} upcoming, {past} past).")


if __name__ == "__main__":
    main()
