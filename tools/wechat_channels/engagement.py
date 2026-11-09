"""Strict public engagement values; unavailable counts stay explicitly unknown."""
from __future__ import annotations

import math

ENGAGEMENT_FIELDS = ('recommend', 'share', 'like', 'comment')
MAX_COUNT = (1 << 53) - 1
# Verified against the same video's native UI: thumbs-up 4302, share 2380,
# heart 3489, comments 304 correspond to these exact feed properties.
FEED_COUNT_FIELDS = {'recommend': 'favCount', 'share': 'forwardCount',
                     'like': 'likeCount', 'comment': 'commentCount'}


def valid_count(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and (not math.isfinite(value) or not value.is_integer()):
        return None
    if not 0 <= value <= MAX_COUNT:
        return None
    return int(value)


def normalize_engagement(value) -> dict:
    value = value if isinstance(value, dict) else {}
    return {field: valid_count(value.get(field)) for field in ENGAGEMENT_FIELDS}


def merge_engagement(previous, incoming) -> dict:
    """Use known new counts even when they decrease; preserve known on missing."""
    old, new = normalize_engagement(previous), normalize_engagement(incoming)
    return {field: new[field] if new[field] is not None else old[field]
            for field in ENGAGEMENT_FIELDS}


def engagement_from_feed(feed) -> dict:
    feed = feed if isinstance(feed, dict) else {}
    return {field: valid_count(feed.get(raw_field))
            for field, raw_field in FEED_COUNT_FIELDS.items()}
