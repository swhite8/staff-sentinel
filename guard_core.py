"""Configuration, conservative name matching, and persistent rate limits."""
import json
import re
import sqlite3
import time
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path


def normalize(name):
    # NFKC catches full-width characters; this is not a complete confusable detector.
    value = unicodedata.normalize("NFKC", name).casefold()
    return "".join(c for c in value if c.isalnum())


def match_names(names, protected, threshold=0.88):
    matches = []
    for name in set(names):
        candidate = normalize(name)
        if not candidate:
            continue
        for target in set(protected):
            expected = normalize(target)
            if not expected:
                continue
            score = SequenceMatcher(None, candidate, expected).ratio()
            if candidate == expected or (min(len(candidate), len(expected)) >= 5 and score >= threshold):
                matches.append((name, target, score))
    return sorted(matches, key=lambda x: x[2], reverse=True)[:5]


def snowflake(value):
    if not re.fullmatch(r"[0-9]{15,20}", str(value)):
        raise ValueError("Discord IDs must contain 15–20 digits")
    number = int(value)
    if not 0 < number < 2**64:
        raise ValueError("Discord ID out of range")
    return number


def message_link(value):
    return bool(re.fullmatch(r"https://(?:www\.)?discord(?:app)?\.com/channels/(?:@me|[0-9]{15,20})/[0-9]{15,20}/[0-9]{15,20}", value))


def load_config(path):
    config = json.loads(Path(path).read_text())
    for key in ("guild_id", "moderator_channel_id"):
        config[key] = snowflake(config[key])
    config["employee_ids"] = {snowflake(x) for x in config["employee_ids"]}
    if not config["employee_ids"]:
        raise ValueError("Configure at least one real employee account ID")
    names = config.get("protected_names", [])
    if not isinstance(names, list) or any(not isinstance(x, str) or not normalize(x) for x in names):
        raise ValueError("protected_names must be a list of nonempty names")
    config["protected_names"] = names
    threshold = config.setdefault("similarity_threshold", 0.88)
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not 0.7 <= threshold <= 1:
        raise ValueError("similarity_threshold must be between 0.7 and 1")
    for key, default in (("alert_cooldown_seconds", 21600), ("report_cooldown_seconds", 60)):
        value = config.setdefault(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{key} must be a positive integer")
    if not isinstance(config.setdefault("welcome_dm", True), bool):
        raise ValueError("welcome_dm must be true or false")
    config.setdefault("database_path", "guard.sqlite3")
    if not isinstance(config.setdefault("avatar_matching", True), bool):
        raise ValueError("avatar_matching must be true or false")
    distance = config.setdefault("avatar_max_hash_distance", 20)
    if isinstance(distance, bool) or not isinstance(distance, int) or not 0 <= distance <= 64:
        raise ValueError("avatar_max_hash_distance must be an integer from 0 to 64")
    error = config.setdefault("avatar_max_pixel_error", 0.12)
    if isinstance(error, bool) or not isinstance(error, (int, float)) or not 0 <= error <= 0.3:
        raise ValueError("avatar_max_pixel_error must be between 0 and 0.3")
    return config


class Cooldowns:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute("CREATE TABLE IF NOT EXISTS cooldowns (key TEXT PRIMARY KEY, sent REAL NOT NULL)")
        self.db.commit()

    def remaining(self, key, seconds):
        row = self.db.execute("SELECT sent FROM cooldowns WHERE key=?", (key,)).fetchone()
        return max(0, seconds - (time.time() - row[0])) if row else 0

    def mark(self, key):
        now = time.time()
        with self.db:
            self.db.execute("DELETE FROM cooldowns WHERE sent < ?", (now - 30 * 86400,))
            self.db.execute("INSERT OR REPLACE INTO cooldowns VALUES (?, ?)", (key, now))
