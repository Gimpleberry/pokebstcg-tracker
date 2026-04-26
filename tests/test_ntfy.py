#!/usr/bin/env python3
"""
ntfy Test — run this to verify your push notifications are working.
Usage: python test_ntfy.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import requests

print("=" * 50)
print("ntfy.sh Notification Test")
print("=" * 50)

# Try to load topic from tracker.py
topic = ""
try:
    from tracker import CONFIG
    topic = CONFIG.get("ntfy_topic", "")
    print(f"\nFound topic in tracker.py: '{topic}'")
except ImportError:
    print("\nCould not load tracker.py — enter your topic manually")

if not topic or topic == "tcg-restock-MY-SECRET-TOPIC-123":
    topic = input("\nEnter your ntfy topic name: ").strip()

if not topic:
    print("No topic entered. Exiting.")
    sys.exit(1)

print(f"\nSending test notification to topic: '{topic}'")
print("Check your phone for the notification...\n")

try:
    r = requests.post(
        f"https://ntfy.sh/{topic}",
        data="Test notification from Keith's PokeBS tracker! If you see this, ntfy is working correctly.".encode("utf-8"),
        headers={
            "Title": "PokeBS Tracker - Test Notification",
            "Priority": "high",
            "Tags": "white_check_mark,tada",
            "Content-Type": "text/plain; charset=utf-8",
        },
        timeout=10,
    )
    if r.status_code == 200:
        print(f"✅ Notification sent successfully! (HTTP {r.status_code})")
        print("   Check your phone — you should see a notification within a few seconds.")
        print()
        print("If you don't see it:")
        print("  1. Open the ntfy app on your phone")
        print(f"  2. Make sure you are subscribed to the topic: '{topic}'")
        print("  3. Check that notifications are enabled for the ntfy app in your phone Settings")
        print("  4. Try pulling down to refresh in the ntfy app")
    else:
        print(f"❌ Unexpected response: HTTP {r.status_code}")
        print(f"   Response: {r.text}")
        print()
        print("This usually means the topic name has invalid characters.")
        print("Topic names can only contain letters, numbers, hyphens, and underscores.")
except requests.exceptions.ConnectionError:
    print("❌ Connection error — check your internet connection.")
except requests.exceptions.Timeout:
    print("❌ Request timed out — ntfy.sh may be temporarily down. Try again in a minute.")
except Exception as e:
    print(f"❌ Error: {e}")

print()
print("If you need to update your topic, open tracker.py in Notepad and find:")
print('  "ntfy_topic": "your-topic-here",')
print("Then update it to match exactly what you subscribed to in the ntfy app.")
