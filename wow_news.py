import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo
from email.utils import parsedate_to_datetime

import discord
import feedparser
from discord.ext import tasks

TOKEN = "DIN_DISCORD_TOKEN"

WOW_NEWS_CHANNEL_ID = 111111111111111111
BLUE_TRACKER_CHANNEL_ID = 222222222222222222
TBC_CHANNEL_ID = 333333333333333333

STATE_FILE = "rss_state.json"
QUEUE_FILE = "rss_queue.json"

TIMEZONE = ZoneInfo("Europe/Stockholm")
SEND_HOUR = 7   # ändra till 8 om du vill ha 08:00
SEND_MINUTE = 0

FEEDS = [
    {
        "name": "wow_news",
        "url": "https://www.wowhead.com/news/rss",
        "channel_id": WOW_NEWS_CHANNEL_ID,
        "author": "Wowhead News",
    },
    {
        "name": "blue_tracker",
        "url": "https://www.wowhead.com/blue-tracker?rss",
        "channel_id": BLUE_TRACKER_CHANNEL_ID,
        "author": "Wowhead Blue Tracker",
    }
]

TBC_KEYWORDS = [
    "tbc",
    "burning crusade",
    "the burning crusade",
    "outland",
    "illidan",
    "karazhan",
    "black temple",
    "sunwell",
    "serpentshrine",
    "tempest keep"
]


def load_json_file(filename, default_value):
    if not os.path.exists(filename):
        return default_value
    try:
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default_value


def save_json_file(filename, data):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def is_tbc_related(entry):
    title = entry.get("title", "").lower()
    summary = entry.get("summary", "").lower()
    combined = f"{title} {summary}"
    return any(keyword in combined for keyword in TBC_KEYWORDS)


def entry_to_dict(entry, source_name):
    return {
        "title": entry.get("title", "Okänd titel"),
        "link": entry.get("link", ""),
        "summary": entry.get("summary", ""),
        "published": entry.get("published", ""),
        "source": source_name
    }


class WowheadClient(discord.Client):
    async def setup_hook(self):
        self.state = load_json_file(STATE_FILE, {})
        self.queue = load_json_file(
            QUEUE_FILE,
            {
                "wow_news": [],
                "blue_tracker": [],
                "tbc_news": [],
                "last_digest_date": ""
            }
        )
        check_rss.start()
        daily_digest_loop.start()

    async def on_ready(self):
        print(f"Inloggad som {self.user} (ID: {self.user.id})")


intents = discord.Intents.default()
client = WowheadClient(intents=intents)


@tasks.loop(minutes=5)
async def check_rss():
    state_changed = False
    queue_changed = False

    for feed_config in FEEDS:
        feed_name = feed_config["name"]
        feed_url = feed_config["url"]

        feed = feedparser.parse(feed_url)

        if not feed.entries:
            print(f"Inga poster hittades i {feed_name}")
            continue

        latest = feed.entries[0]
        latest_link = latest.get("link")

        if not latest_link:
            print(f"Senaste posten i {feed_name} saknar länk")
            continue

        previous_link = client.state.get(feed_name)

        # Första start: spara bara senaste länk utan att fylla kön bakåt i tiden
        if previous_link is None:
            client.state[feed_name] = latest_link
            state_changed = True
            print(f"Startvärde satt för {feed_name}: {latest.get('title', 'okänd post')}")
            continue

        if latest_link != previous_link:
            client.state[feed_name] = latest_link
            state_changed = True

            item = entry_to_dict(latest, feed_name)

            if feed_name == "wow_news":
                client.queue["wow_news"].append(item)
                queue_changed = True
                print(f"Köade wow_news: {item['title']}")

                if is_tbc_related(latest):
                    client.queue["tbc_news"].append(item)
                    queue_changed = True
                    print(f"Köade tbc_news: {item['title']}")

            elif feed_name == "blue_tracker":
                client.queue["blue_tracker"].append(item)
                queue_changed = True
                print(f"Köade blue_tracker: {item['title']}")

    if state_changed:
        save_json_file(STATE_FILE, client.state)

    if queue_changed:
        save_json_file(QUEUE_FILE, client.queue)


async def send_digest_to_channel(channel_id, title, items):
    channel = client.get_channel(channel_id)
    if channel is None:
        print(f"Kunde inte hitta kanal: {channel_id}")
        return

    if not items:
        return

    lines = []
    for item in items:
        lines.append(f"• **{item['title']}**\n{item['link']}")

    message_chunks = []
    current_chunk = f"## {title}\n\n"

    for line in lines:
        if len(current_chunk) + len(line) + 2 > 1900:
            message_chunks.append(current_chunk)
            current_chunk = line + "\n\n"
        else:
            current_chunk += line + "\n\n"

    if current_chunk.strip():
        message_chunks.append(current_chunk)

    for chunk in message_chunks:
        await channel.send(chunk)


@tasks.loop(minutes=1)
async def daily_digest_loop():
    now = datetime.now(TIMEZONE)
    today_str = now.strftime("%Y-%m-%d")

    if now.hour == SEND_HOUR and now.minute == SEND_MINUTE:
        if client.queue.get("last_digest_date") == today_str:
            return

        print(f"Skickar dagsrapport för {today_str}")

        await send_digest_to_channel(
            WOW_NEWS_CHANNEL_ID,
            f"Wowhead Daily Digest – {today_str}",
            client.queue.get("wow_news", [])
        )

        await send_digest_to_channel(
            BLUE_TRACKER_CHANNEL_ID,
            f"Blue Tracker Daily Digest – {today_str}",
            client.queue.get("blue_tracker", [])
        )

        await send_digest_to_channel(
            TBC_CHANNEL_ID,
            f"TBC / Classic Daily Digest – {today_str}",
            client.queue.get("tbc_news", [])
        )

        client.queue["wow_news"] = []
        client.queue["blue_tracker"] = []
        client.queue["tbc_news"] = []
        client.queue["last_digest_date"] = today_str

        save_json_file(QUEUE_FILE, client.queue)
        print("Dagsrapport skickad och kö tömd.")


@check_rss.before_loop
async def before_check_rss():
    await client.wait_until_ready()


@daily_digest_loop.before_loop
async def before_daily_digest_loop():
    await client.wait_until_ready()


client.run(TOKEN)