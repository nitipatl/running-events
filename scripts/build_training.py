"""
Build training.json from the private "workout-plan" Google Calendar ICS.

PRIVACY: outputs ONLY date, start, title (SUMMARY). Never descriptions,
locations, attendees, URLs or UIDs (descriptions may contain PNRs / bookings).

Window: Monday of the current week (Asia/Bangkok) + 14 days.
If WORKOUT_ICS_URL is unset/empty, exits 0 without touching training.json.
"""

import json
import os
import sys
from datetime import date, datetime, timedelta
from urllib.request import urlopen
from zoneinfo import ZoneInfo

BKK = ZoneInfo("Asia/Bangkok")
OUT = "training.json"


def clean(s) -> str:
    return " ".join(str(s or "").split())


def main():
    url = os.environ.get("WORKOUT_ICS_URL", "").strip()
    if not url:
        print("WORKOUT_ICS_URL not set — leaving training.json untouched.")
        return 0

    from icalendar import Calendar
    import recurring_ical_events

    now = datetime.now(BKK)
    monday = now.date() - timedelta(days=now.weekday())
    win_start = datetime(monday.year, monday.month, monday.day, tzinfo=BKK)
    win_end = win_start + timedelta(days=14)

    with urlopen(url, timeout=30) as resp:
        cal = Calendar.from_ical(resp.read())

    items = []
    for ev in recurring_ical_events.of(cal).between(win_start, win_end):
        if ev.name != "VEVENT":
            continue
        if clean(ev.get("STATUS")).upper() == "CANCELLED":
            continue
        dt = ev.get("DTSTART").dt
        if isinstance(dt, datetime):
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=BKK)
            dt = dt.astimezone(BKK)
            d, start = dt.date(), dt.strftime("%H:%M")
        else:
            d, start = dt, ""
        if not (monday <= d < monday + timedelta(days=14)):
            continue
        # ONLY these three fields — nothing else from the event is read out.
        items.append({"date": d.isoformat(), "start": start, "title": clean(ev.get("SUMMARY"))})

    items.sort(key=lambda i: (i["date"], i["start"]))
    data = {"updated": now.replace(microsecond=0).isoformat(), "items": items}

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print(f"Done. {len(items)} training items written to {OUT} ({monday} → {monday + timedelta(days=13)}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
