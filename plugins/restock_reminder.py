#!/usr/bin/env python3
"""
restock_reminder.py - Daily Restock Reminder (#8)
Plugin for Keith's PokeBS tracker system.

Fires a push notification every morning at 8:30 AM with a
day-specific message based on known restock patterns:

  Monday    - Low activity. Check Target overnight results.
  Tuesday   - Vendor route day. Target & Walmart shelves restocked.
              Check in-store before work/lunch.
  Wednesday - WALMART WEDNESDAY. Public drop 12 PM ET.
              Walmart+ early access 9 AM ET.
  Thursday  - Post-Wednesday cleanup. Target may restock overnight.
  Friday    - TARGET RESTOCK DAY. 3-6 PM ET window.
              Best Buy in-store restocks.
  Saturday  - Weekend check. ALDI Finds rotate. Check local stores.
  Sunday    - Quiet day. Review your watchlist for the week ahead.

Special overrides:
  - Set launch days get a HIGH PRIORITY alert regardless of day
  - Prerelease weekends get a reminder to check local game stores
  - Days within 3 days of a known drop get an early heads-up

All messaging is plain text - no emojis in notification body to
avoid encoding issues. Notification title uses tags for icons.
"""

import os
import sys
import logging
from datetime import datetime, date, timedelta

# ── Path resolution ───────────────────────────────────────────────────────────
_here = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_here) if os.path.basename(_here) == "plugins" else _here
if _root not in sys.path:
    sys.path.insert(0, _root)
if _here not in sys.path:
    sys.path.insert(0, _here)
# ─────────────────────────────────────────────────────────────────────────────

from shared import send_ntfy, load_history, save_history

log = logging.getLogger(__name__)

HISTORY_FILE = "restock_reminder_history.json"
FIRE_TIME    = "08:30"  # 24h format - change here to adjust time


# ── Known upcoming drops (kept in sync with future.html data) ────────────────
# Format: (date_str YYYY-MM-DD, product_name, priority)
# priority: 'high' = launch day, 'medium' = major wave, 'low' = general
KNOWN_DROPS = [
    ("2026-05-22", "Chaos Rising - Full Launch (Target ~3AM, PC ~10AM)",   "high"),
    ("2026-05-27", "Chaos Rising - Walmart Wednesday Drop",                "high"),
    ("2026-07-17", "Pitch Black (Mega Darkrai ex) - Launch Day",           "high"),
    ("2026-09-18", "30th Celebration Set - Worldwide Simultaneous Launch", "high"),
    ("2026-05-09", "Chaos Rising Prerelease Begins (Local Game Stores)",   "medium"),
    ("2026-06-19", "First Partner Illustration Collection Series 2",       "medium"),
    ("2026-07-04", "Pitch Black Prerelease Begins (Local Game Stores)",    "medium"),
    ("2026-10-16", "30th Celebration Card Sets x9",                       "medium"),
]


# ── Day-specific messages ─────────────────────────────────────────────────────

DAY_PROFILES = {
    0: {  # Monday
        "label":    "Monday",
        "priority": "default",
        "tags":     "calendar,eyes",
        "headline": "Monday - Low activity day",
        "lines": [
            "Check if Target had any overnight drops last night.",
            "Review your watchlist - anything close to MSRP?",
            "Good day to check store inventory: python plugins/store_inventory.py",
        ],
        "retailers": [],
    },
    1: {  # Tuesday
        "label":    "Tuesday",
        "priority": "high",
        "tags":     "calendar,department_store",
        "headline": "Tuesday - Vendor route day",
        "lines": [
            "Target and Walmart shelf crews run their routes today.",
            "Check your local Target and Walmart in-store - mid-morning is best.",
            "Alt retailer scan runs automatically at 9 AM (Five Below, Marshalls, ALDI).",
            "Target online restocks sometimes go live Tuesday overnight.",
        ],
        "retailers": ["target", "walmart"],
    },
    2: {  # Wednesday
        "label":    "Wednesday",
        "priority": "urgent",
        "tags":     "rotating_light,shopping_cart",
        "headline": "WALMART WEDNESDAY - Drop day",
        "lines": [
            "Walmart public drop: 12:00 PM ET",
            "Walmart+ early access: 9:00 AM ET (3 hrs early)",
            "Tracker is monitoring - watch for ntfy alerts around noon.",
            "Have walmart.com open and payment saved before 12 PM.",
            "Filter your dashboard by Walmart to focus on today.",
        ],
        "retailers": ["walmart"],
    },
    3: {  # Thursday
        "label":    "Thursday",
        "priority": "default",
        "tags":     "calendar,eyes",
        "headline": "Thursday - Post-Wednesday check",
        "lines": [
            "Check if Walmart Wednesday had any restocks you missed.",
            "Target may have had overnight restocks after Wednesday vendor runs.",
            "Best Buy in-store restocks sometimes happen Thursday.",
        ],
        "retailers": ["target", "bestbuy"],
    },
    4: {  # Friday
        "label":    "Friday",
        "priority": "high",
        "tags":     "rotating_light,tada",
        "headline": "Friday - TARGET RESTOCK DAY",
        "lines": [
            "Target's highest-probability restock window: 3:00 PM - 6:00 PM ET",
            "Tracker ramps up to 3-min checks from 2 PM today automatically.",
            "Best Buy in-store restocks are common on Fridays.",
            "Keep your phone nearby this afternoon - alerts could fire any time.",
        ],
        "retailers": ["target", "bestbuy"],
    },
    5: {  # Saturday
        "label":    "Saturday",
        "priority": "default",
        "tags":     "calendar,department_store",
        "headline": "Saturday - Weekend in-store check",
        "lines": [
            "ALDI Finds rotate on Wednesdays but leftovers show up Saturday.",
            "Good day to visit Target, Walmart, and Five Below in person.",
            "Check Marshalls and TJ Maxx - weekend is a good time to browse.",
            "Pokemon Center occasionally drops weekend exclusives.",
        ],
        "retailers": ["pokemoncenter"],
    },
    6: {  # Sunday
        "label":    "Sunday",
        "priority": "low",
        "tags":     "calendar,clipboard",
        "headline": "Sunday - Plan your week",
        "lines": [
            "Quiet day for restocks - good time to review.",
            "Check the Future page for any drops coming this week.",
            "Confirm your ntfy alerts are working: python tests/test_ntfy.py",
            "Tuesday and Wednesday are the big days this week.",
        ],
        "retailers": [],
    },
}


# ── Upcoming drop awareness ───────────────────────────────────────────────────

def _get_upcoming_drops(today: date) -> list[dict]:
    """Return any drops within the next 7 days."""
    upcoming = []
    for date_str, name, priority in KNOWN_DROPS:
        try:
            drop_date = date.fromisoformat(date_str)
            diff = (drop_date - today).days
            if 0 <= diff <= 7:
                upcoming.append({
                    "date":     date_str,
                    "name":     name,
                    "priority": priority,
                    "days_away": diff,
                    "label":    "TODAY" if diff == 0 else
                                f"TOMORROW ({drop_date.strftime('%a')})" if diff == 1 else
                                # Portable date format (v6.0.0 step 4.8.6) - see fix #1 comment
                                f"In {diff} days ({drop_date.strftime('%a %b')} {drop_date.day})",
                })
        except ValueError:
            continue
    return sorted(upcoming, key=lambda x: x["days_away"])


# ── Build notification ────────────────────────────────────────────────────────

def build_reminder(today: date) -> dict:
    """
    Build the day-appropriate reminder notification.
    Returns dict with: title, body, priority, tags, url
    """
    weekday  = today.weekday()  # 0=Mon, 6=Sun
    profile  = DAY_PROFILES[weekday]
    upcoming = _get_upcoming_drops(today)

    # Check for launch day override
    today_drops = [d for d in upcoming if d["days_away"] == 0]
    high_priority_today = [d for d in today_drops if d["priority"] == "high"]

    if high_priority_today:
        # Launch day - override everything with urgent alert
        drop = high_priority_today[0]
        return {
            "title":    f"LAUNCH DAY: {drop['name'][:45]}",
            "body":     (
                # Portable date format (v6.0.0 step 4.8.6) - see fix #1 comment
                f"LAUNCH DAY - {today.strftime('%A %B')} {today.day}\n"
                f"\n"
                f"{drop['name']}\n"
                f"\n"
                f"Tracker is on full alert. Watch your ntfy notifications.\n"
                f"Keep your computer on and unlocked all day."
            ),
            "priority": "urgent",
            "tags":     "rotating_light,tada,fire",
            "url":      "http://localhost:8080/dashboard/dashboard.html",
        }

    # Standard day profile
    lines = [
        f"Good morning - {profile['headline']}",
        "",
    ]
    lines.extend(profile["lines"])

    # Append upcoming drops section if any in next 7 days
    if upcoming:
        lines.append("")
        lines.append("Upcoming drops this week:")
        for drop in upcoming[:3]:
            lines.append(f"  {drop['label']}: {drop['name'][:55]}")

    return {
        "title":    f"PokeBS Morning: {profile['headline']}",
        "body":     "\n".join(lines),
        "priority": profile["priority"],
        "tags":     profile["tags"],
        "url":      "http://localhost:8080/dashboard/dashboard.html",
    }


# ── Core send function ────────────────────────────────────────────────────────

def send_reminder(config: dict) -> None:
    """Build and send today's restock reminder."""
    today   = date.today()
    history = load_history(HISTORY_FILE)

    # Deduplicate - only send once per day
    today_key = today.isoformat()
    if history.get(today_key, {}).get("sent"):
        log.debug(f"[restock_reminder] Already sent for {today_key} - skipping")
        return

    ntfy_topic = config.get("ntfy_topic", "")
    reminder   = build_reminder(today)

    log.info(
        f"[restock_reminder] Sending {today.strftime('%A')} reminder - "
        f"priority={reminder['priority']}"
    )

    success = send_ntfy(
        topic=ntfy_topic,
        title=reminder["title"],
        body=reminder["body"],
        url=reminder["url"],
        priority=reminder["priority"],
        tags=reminder["tags"],
    )

    if success:
        history[today_key] = {
            "sent":       True,
            "sent_at":    datetime.now().isoformat(),
            "day":        today.strftime("%A"),
            "priority":   reminder["priority"],
        }
        save_history(HISTORY_FILE, history)
        # Portable date formatting (v6.0.0 step 4.8.6): the POSIX-only
        # day-without-zero-pad strftime directive crashes on Windows with
        # ValueError. Build the string manually using today.day, which
        # works on both Windows and POSIX systems.
        log.info(f"[restock_reminder] Sent successfully for {today.strftime('%A %B')} {today.day}")
    else:
        log.warning("[restock_reminder] Send failed - will retry next schedule cycle")


# ── Plugin class (used by plugins.py) ────────────────────────────────────────

class RestockReminder:
    """Plugin wrapper - registered via plugins.py RestockReminder_Plugin."""

    def __init__(self, config: dict):
        self.config = config

    def start(self, schedule) -> None:
        schedule.every().day.at(FIRE_TIME).do(send_reminder, self.config)
        log.info(f"[restock_reminder] Scheduled daily at {FIRE_TIME}")

    def stop(self) -> None:
        log.info("[restock_reminder] Stopped")


# ── Standalone diagnostic ─────────────────────────────────────────────────────

def run_diagnostics(config: dict) -> None:
    """
    Preview today's reminder without sending it.
    Usage: python plugins/restock_reminder.py
    Options:
      --send    Actually send the notification
      --day MON Preview a specific day (MON/TUE/WED/THU/FRI/SAT/SUN)
    """
    import sys

    send_flag = "--send" in sys.argv
    day_flag  = None
    if "--day" in sys.argv:
        idx = sys.argv.index("--day")
        if idx + 1 < len(sys.argv):
            day_map = {
                "MON": 0, "TUE": 1, "WED": 2, "THU": 3,
                "FRI": 4, "SAT": 5, "SUN": 6,
            }
            day_flag = day_map.get(sys.argv[idx + 1].upper())

    print("\n" + "=" * 60)
    print("  Restock Reminder - Diagnostic")
    print("=" * 60)

    # Show all 7 day previews
    base = date.today()
    if day_flag is not None:
        days_to_show = [day_flag]
        print(f"\n  Previewing {list(DAY_PROFILES.keys())[day_flag]} "
              f"({DAY_PROFILES[day_flag]['label']}):\n")
    else:
        days_to_show = range(7)
        print("\n  7-day preview:\n")

    for wd in days_to_show:
        # Find the next occurrence of this weekday
        days_ahead = (wd - base.weekday()) % 7
        preview_date = base + timedelta(days=days_ahead)
        reminder = build_reminder(preview_date)

        print(f"  {'─'*55}")
        print(f"  {reminder['title']}")
        print(f"  Priority: {reminder['priority']}  |  Tags: {reminder['tags']}")
        print()
        for line in reminder["body"].split("\n"):
            print(f"    {line}")
        print()

    if send_flag:
        print("  Sending today's reminder NOW...")
        send_reminder(config)
        print("  Done - check your phone.")
    else:
        print("  Run with --send to actually send today's reminder.")
        print("  Run with --day WED to preview a specific day.\n")

    print("=" * 60 + "\n")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler()],
    )
    try:
        from tracker import CONFIG

        # Verify ntfy topic before running
        topic = CONFIG.get("ntfy_topic", "")
        placeholder = "tcg-restock-MY-SECRET-TOPIC-123"

        print(f"\n  ntfy topic loaded: '{topic[:4]}****{topic[-4:]}'" if len(topic) > 8
              else f"\n  ntfy topic: '{topic}'")

        if not topic:
            print("  ERROR: ntfy_topic is empty in tracker.py CONFIG")
            print("  Open tracker.py and set your ntfy topic, then retry.")
        elif topic == placeholder:
            print("  ERROR: ntfy_topic is still the placeholder value.")
            print(f"  Open tracker.py and replace '{placeholder}'")
            print("  with your actual ntfy topic name, then retry.")
        else:
            print("  ntfy topic looks good - proceeding.\n")
            run_diagnostics(CONFIG)

    except ImportError:
        log.error("Run from tcg_tracker/ directory: python plugins/restock_reminder.py")
