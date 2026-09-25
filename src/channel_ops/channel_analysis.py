"""Judge the channel's numbers, rather than only printing them.

The audit answers "what are the figures". This answers the three questions
that actually change what we do next:

  1. How far are we from the Partner Programme, and at the measured rate, when?
  2. Which of the things we vary actually moves the numbers?
  3. Which videos are failing, and what do they have in common?

WHY THE STATISTICS ARE STRICT. Four times on this channel a difference was
read off a handful of videos, called a finding, acted on, and withdrawn later:
"volume causes dead videos" (25 Aug), "the owl's like rate is a warning"
(7 Sep), "machines outperform animals" (10 Sep), "compilations will produce
the watch hours" (18 Sep). The 24 August one cost nineteen hitless videos and
a 44% decline. Views on this channel are wildly skewed -- one video is 57% of
all of them -- so eyeballing two medians is worse than useless.

So no comparison in here is allowed to report a difference on judgement. It
must clear a minimum group size AND a rank-sum test, or it reports "not enough
data" / "no difference". A system that cannot say "I don't know" will keep
telling you what you hoped to hear.

WHAT CANNOT BE MEASURED HERE, and must not be faked: impressions and
click-through rate. The Analytics API refuses them -- ``HTTP 400 Unknown
identifier (impressions)`` -- they exist only in Studio. Thumbnail and title
decisions cannot be automated against CTR. ``studio_manual.json`` exists so
figures read off Studio by hand can at least enter the record.
"""
from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import median

from .channel_data import SHORT_MAX_SECONDS, per_video
from .config_loader import find_project_root

METRICS_FILE = "data/channel_metrics.json"
STUDIO_FILE = "data/studio_manual.json"

# The Partner Programme has TWO tiers and only the upper one pays for ads.
# Tracking the upper one alone was a reporting mistake: it showed 17% when the
# nearest real milestone was at 58%, which is the difference between "far off"
# and "more than halfway".
#
#   Early access -- fan funding only (Super Thanks, memberships, Shopping).
#   NO advertising revenue.
#     500 subscribers
#     + 3 public uploads in the last 90 days
#     + (3,000 watch hours in 12 months OR 3 million Shorts views in 90 days)
#
#   Full programme -- advertising revenue.
#     1,000 subscribers
#     + (4,000 watch hours in 12 months OR 10 million Shorts views in 90 days)
#
# Within each tier the subscriber count and the watch-time route are both
# required; the two watch-time routes are the alternatives. Presenting them as
# independent paths once already sent this channel down one that was closed.
#
# These figures come from the programme's published rules and YouTube changes
# them from time to time. They are worth re-checking in Studio against what it
# shows for this channel rather than trusted indefinitely from here.
EARLY_SUBSCRIBER_GOAL = 500
EARLY_WATCH_HOUR_GOAL = 3_000
EARLY_SHORTS_VIEW_GOAL = 3_000_000
EARLY_UPLOAD_GOAL = 3

SUBSCRIBER_GOAL = 1_000
WATCH_HOUR_GOAL = 4_000
SHORTS_VIEW_GOAL = 10_000_000
SHORTS_VIEW_WINDOW_DAYS = 90

# A video is judged only once YouTube has finished deciding about it. Before
# two days it is still being served and its numbers are not yet an outcome.
MATURITY_HOURS = 48
DEAD_VIEWS = 250
HIT_VIEWS = 10_000

# Comparison thresholds. MIN_GROUP is per side, not total.
MIN_GROUP = 8
SIGNIFICANCE = 0.05

# Past this, a projected date is not a forecast. At one point the watch-hour
# gate was growing slowly enough to produce "2122-07-17", which is arithmetic,
# not information: it invites planning around a date instead of reading it as
# the refusal it is.
MAX_FORECAST_DAYS = 365 * 2

# Subjects split into living things and made things. Keyword matching, which is
# crude: it is reported alongside every verdict that uses it so nobody mistakes
# it for a classification YouTube supplied.
_MACHINE_WORDS = frozenset({
    "engine", "locomotive", "winch", "pump", "camera", "sextant", "theodolite",
    "orrery", "anemometer", "excavator", "clockwork", "machine", "compass",
    "telescope", "turbine", "gramophone", "typewriter", "astrolabe", "loom",
})


# -----------------------------------------------------------------------
# Statistics
# -----------------------------------------------------------------------

def mann_whitney_p(first: list[float], second: list[float]) -> float:
    """Two-sided p-value for "these two samples came from the same place".

    A rank test rather than a mean test because view counts on this channel are
    not remotely normal -- a single 594,000-view video sits in a set whose
    median is about 2,000, and any test built on means is really just reporting
    where that one video landed.

    Normal approximation with a tie correction. Exact enough from about eight
    per side, which is the same floor MIN_GROUP sets, so the two agree.
    """
    n1, n2 = len(first), len(second)
    if n1 == 0 or n2 == 0:
        return 1.0

    merged = sorted([(value, 0) for value in first] + [(value, 1) for value in second])
    ranks: list[float] = [0.0] * len(merged)
    index = 0
    tie_adjustment = 0.0
    while index < len(merged):
        stop = index
        while stop + 1 < len(merged) and merged[stop + 1][0] == merged[index][0]:
            stop += 1
        average_rank = (index + stop) / 2 + 1
        for position in range(index, stop + 1):
            ranks[position] = average_rank
        run = stop - index + 1
        if run > 1:
            tie_adjustment += run ** 3 - run
        index = stop + 1

    rank_sum = sum(rank for rank, (_, group) in zip(ranks, merged) if group == 0)
    u1 = rank_sum - n1 * (n1 + 1) / 2
    u2 = n1 * n2 - u1
    u = min(u1, u2)

    total = n1 + n2
    mean = n1 * n2 / 2
    variance = n1 * n2 / 12 * ((total + 1) - tie_adjustment / (total * (total - 1)))
    if variance <= 0:
        return 1.0
    # Continuity correction; without it small samples read as more certain than
    # they are, which is the exact failure this whole module exists to prevent.
    z = (abs(u - mean) - 0.5) / math.sqrt(variance)
    return 2 * (1 - 0.5 * (1 + math.erf(z / math.sqrt(2))))


def compare(label_a: str, values_a: list[float], label_b: str, values_b: list[float]) -> dict:
    """One honest verdict about whether two groups differ."""
    n_a, n_b = len(values_a), len(values_b)
    result = {
        "a": label_a, "b": label_b, "n_a": n_a, "n_b": n_b,
        "median_a": round(median(values_a), 1) if values_a else None,
        "median_b": round(median(values_b), 1) if values_b else None,
        "p": None,
    }
    if n_a < MIN_GROUP or n_b < MIN_GROUP:
        result["verdict"] = f"yeterli veri yok (en az {MIN_GROUP}+{MIN_GROUP} gerekiyor)"
        return result
    p = mann_whitney_p(values_a, values_b)
    result["p"] = round(p, 4)
    if p >= SIGNIFICANCE:
        result["verdict"] = "fark yok"
        return result
    better = label_a if result["median_a"] > result["median_b"] else label_b
    result["verdict"] = f"{better} daha iyi (p={p:.3f})"
    return result


# -----------------------------------------------------------------------
# Shaping the raw dump into one row per video
# -----------------------------------------------------------------------

def _age_hours(published_at: str, now: datetime) -> float:
    try:
        stamp = datetime.fromisoformat(published_at)
    except (TypeError, ValueError):
        return 0.0
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return (now - stamp).total_seconds() / 3600


def _per_thousand(count, views) -> float | None:
    """Rates, not raw counts. A hit collects more likes by being a hit; the
    question is whether people who watched it liked it more."""
    if not views:
        return None
    try:
        return round(float(count) * 1000 / views, 2)
    except (TypeError, ValueError):
        return None


def videos(dump: dict, now: datetime | None = None) -> list[dict]:
    """One flat row per Short, with the derived rates the analysis needs."""
    now = now or datetime.now(UTC)
    stats = dump.get("data_api") or {}
    live = per_video(dump)
    out = []
    for record in dump.get("published") or []:
        vid = record.get("youtube_video_id", "")
        if not vid:
            continue
        data = stats.get(vid, {})
        row = live.get(vid, {})
        views = data.get("views", 0)
        published_at = record.get("published_at", "")
        age = _age_hours(published_at, now)
        creature = (record.get("creature") or "").lower()
        out.append({
            "id": vid,
            "creature": creature,
            "published_at": published_at,
            "hour": published_at[11:13] if len(published_at) >= 13 else "",
            "weekday": _weekday(published_at),
            "age_hours": round(age, 1),
            "mature": age >= MATURITY_HOURS,
            "views": views,
            "seconds": data.get("seconds", 0),
            "retention": row.get("averageViewPercentage"),
            "watch_seconds": row.get("averageViewDuration"),
            "subs_per_1k": _per_thousand(row.get("subscribersGained"), views),
            "likes_per_1k": _per_thousand(data.get("likes"), views),
            "comments_per_1k": _per_thousand(data.get("comments"), views),
            "shares_per_1k": _per_thousand(row.get("shares"), views),
            "template": (record.get("template_version") or "-")[:8],
            "idea": (record.get("idea_version") or "-")[:8],
            "kind": "makine" if _is_machine(creature) else "canli",
            "dead": age >= MATURITY_HOURS and views < DEAD_VIEWS,
            "hit": views >= HIT_VIEWS,
        })
    return out


def _weekday(published_at: str) -> str:
    try:
        stamp = datetime.fromisoformat(published_at)
    except (TypeError, ValueError):
        return ""
    return ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"][stamp.weekday()]


def _is_machine(creature: str) -> bool:
    return any(word in creature for word in _MACHINE_WORDS)


# -----------------------------------------------------------------------
# The Partner Programme scoreboard
# -----------------------------------------------------------------------

def thresholds(dump: dict, history: list[dict]) -> dict:
    """Where the two gates stand, and when they arrive at the measured rate."""
    totals = dump.get("totals") or {}
    stats = dump.get("data_api") or {}
    live = per_video(dump)

    long_ids = {
        record.get("youtube_video_id")
        for record in dump.get("long_form") or []
    }
    short_views_90d = 0
    long_minutes_90d = 0.0
    for vid, row in live.items():
        seconds = (stats.get(vid) or {}).get("seconds", 0)
        is_long = vid in long_ids or seconds > SHORT_MAX_SECONDS
        if is_long:
            long_minutes_90d += float(row.get("estimatedMinutesWatched") or 0)
        else:
            short_views_90d += int(row.get("views") or 0)

    subscribers = int(totals.get("subscribers") or 0)
    watch_hours = long_minutes_90d / 60

    uploads_90d = _recent_uploads(dump)

    return {
        # The watch-hour goals are counted over twelve months and this window is
        # ninety days. The channel is younger than ninety days, so the two are
        # the same number today; the day that stops being true this figure
        # starts understating and the note has to change with it.
        "window_note": "izlenme saati 90 günlük pencereden; kanal 90 günden genç olduğu sürece = 12 ay",
        "uploads_90d": uploads_90d,
        # Lower tier first: it is the one that is actually near, and a
        # scoreboard that leads with the unreachable number is a scoreboard
        # nobody reads twice.
        "early_subscribers": _gate("Abone (alt kademe)", subscribers,
                                   EARLY_SUBSCRIBER_GOAL, history, "subscribers"),
        "early_watch_hours": _gate("İzlenme saati (alt kademe)", round(watch_hours, 1),
                                   EARLY_WATCH_HOUR_GOAL, history, "watch_hours"),
        "early_shorts_views": _gate(f"{SHORTS_VIEW_WINDOW_DAYS} günlük Shorts (alt kademe)",
                                    short_views_90d, EARLY_SHORTS_VIEW_GOAL,
                                    history, "shorts_views_90d"),
        "subscribers": _gate("Abone", subscribers, SUBSCRIBER_GOAL, history, "subscribers"),
        "watch_hours": _gate("İzlenme saati (uzun video)", round(watch_hours, 1),
                             WATCH_HOUR_GOAL, history, "watch_hours"),
        "shorts_views": _gate(f"{SHORTS_VIEW_WINDOW_DAYS} günlük Shorts izlenmesi",
                              short_views_90d, SHORTS_VIEW_GOAL, history, "shorts_views_90d"),
    }


def _recent_uploads(dump: dict) -> int:
    """Public uploads inside the ninety-day window.

    The lower tier requires three. This channel publishes twice a day, so it is
    never the binding condition -- it is counted anyway because a condition
    nobody checks is a condition nobody notices failing.
    """
    cutoff = datetime.now(UTC) - timedelta(days=SHORTS_VIEW_WINDOW_DAYS)
    count = 0
    for record in (dump.get("published") or []) + (dump.get("long_form") or []):
        try:
            when = datetime.fromisoformat(record.get("published_at", ""))
        except (TypeError, ValueError):
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        if when >= cutoff:
            count += 1
    return count


def _gate(name: str, value: float, goal: float, history: list[dict], key: str) -> dict:
    """One threshold with its progress and, when measurable, an arrival date."""
    gate = {
        "name": name,
        "value": value,
        "goal": goal,
        "percent": round(value / goal * 100, 2) if goal else 0,
        "remaining": round(goal - value, 1),
        "rate_per_day": None,
        "eta": None,
        "note": "",
    }
    rate = _rate_per_day(history, key, value)
    if rate is None:
        gate["note"] = "hız için en az iki ölçüm gerekiyor"
        return gate
    # Two decimals: a gate creeping at 0.04 a day rounds to zero at one, and a
    # rate printed as zero next to a date is a contradiction the reader has to
    # resolve themselves.
    gate["rate_per_day"] = round(rate, 2)
    if value >= goal:
        gate["note"] = "geçildi"
        return gate
    if rate <= 0:
        gate["note"] = "artmıyor — bu hızla ulaşılamaz"
        return gate
    days = (goal - value) / rate
    if days > MAX_FORECAST_DAYS:
        years = days / 365
        gate["note"] = f"bu hızla ulaşılamaz (~{years:.0f} yıl)"
        return gate
    gate["eta"] = (datetime.now(UTC) + timedelta(days=days)).date().isoformat()
    gate["note"] = f"ölçülen hızla {round(days)} gün"
    return gate


def _rate_per_day(history: list[dict], key: str, current: float) -> float | None:
    """Growth per day, measured against the oldest snapshot inside two weeks.

    Against the oldest rather than the previous one because a single viral
    video makes any two-day rate meaningless: on 21 September the channel was
    briefly running at 140,000 views a day, which extrapolated to passing a
    threshold that it is nowhere near.
    """
    now = datetime.now(UTC)
    usable = []
    for snapshot in history:
        try:
            when = datetime.fromisoformat(snapshot["at"])
        except (KeyError, ValueError):
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        if snapshot.get(key) is None:
            continue
        if (now - when) <= timedelta(days=14):
            usable.append((when, float(snapshot[key])))
    if not usable:
        return None
    usable.sort()
    when, value = usable[0]
    days = (now - when).total_seconds() / 86400
    if days < 0.5:
        return None
    return (current - value) / days


# -----------------------------------------------------------------------
# What actually moves the numbers
# -----------------------------------------------------------------------

def cohorts(rows: list[dict], metric: str = "views") -> list[dict]:
    """Every dimension we vary, tested against itself.

    Only mature videos: a video published this morning has not finished being
    distributed, and letting it into a median makes every comparison a
    measurement of how recently we changed something.
    """
    mature = [row for row in rows if row["mature"] and row.get(metric) is not None]
    out = []
    for name, key in (("Şablon", "template"), ("Fikir talimatı", "idea"),
                      ("Yayın saati", "hour"), ("Konu türü", "kind")):
        groups: dict[str, list[float]] = {}
        for row in mature:
            groups.setdefault(row[key], []).append(float(row[metric]))
        ranked = sorted(groups.items(), key=lambda item: -len(item[1]))[:2]
        if len(ranked) < 2:
            continue
        (label_a, values_a), (label_b, values_b) = ranked
        result = compare(label_a, values_a, label_b, values_b)
        result["dimension"] = name
        result["metric"] = metric
        if key == "kind":
            result["method"] = "tür ayrımı anahtar kelimeyle yapıldı, YouTube vermiyor"
        out.append(result)
    return out


def hit_profile(rows: list[dict]) -> dict:
    """What the hits share -- and whether sharing it means anything.

    Every entry carries the same share among the non-hits, because "80% of hits
    are animals" is not a finding when 80% of everything is animals. That
    mistake is what the 24 August rule was built on.
    """
    mature = [row for row in rows if row["mature"]]
    hits = [row for row in mature if row["hit"]]
    rest = [row for row in mature if not row["hit"]]
    profile = {"hit_count": len(hits), "other_count": len(rest), "traits": []}
    if not hits or not rest:
        profile["note"] = "karşılaştırma için iki grup da gerekiyor"
        return profile
    for name, key in (("Şablon", "template"), ("Konu türü", "kind"),
                      ("Yayın saati", "hour"), ("Gün", "weekday")):
        values = {row[key] for row in mature if row[key]}
        for value in sorted(values):
            in_hits = sum(1 for row in hits if row[key] == value) / len(hits)
            in_rest = sum(1 for row in rest if row[key] == value) / len(rest)
            profile["traits"].append({
                "dimension": name,
                "value": value,
                "hit_share": round(in_hits * 100, 1),
                "other_share": round(in_rest * 100, 1),
                "lift": round(in_hits / in_rest, 2) if in_rest else None,
            })
    profile["warning"] = (
        f"{len(hits)} hit, bir kuralı desteklemek için az. Buradaki hiçbir satır "
        "prompt'a kural olarak girmemeli — 24 Ağustos'ta tam olarak bu yapıldı."
    )
    return profile


def weak_videos(rows: list[dict], limit: int = 10) -> list[dict]:
    """The mature videos that underperformed, worst first."""
    mature = [row for row in rows if row["mature"]]
    return sorted(mature, key=lambda row: row["views"])[:limit]


def retention_band(rows: list[dict]) -> dict:
    """Retention for hits against everything else.

    The one number that has separated a hit from a non-hit on this channel
    every time it has been looked at -- which is a reason to watch it, not a
    reason to claim it causes anything.
    """
    mature = [row for row in rows if row["mature"] and row.get("retention") is not None]
    hits = [float(row["retention"]) for row in mature if row["hit"]]
    rest = [float(row["retention"]) for row in mature if not row["hit"]]
    result = compare("hit", hits, "hit değil", rest)
    result["dimension"] = "İzlenme yüzdesi"
    return result


# -----------------------------------------------------------------------
# The record that makes week-over-week possible
# -----------------------------------------------------------------------

def snapshot(dump: dict, rows: list[dict], gates: dict) -> dict:
    """The handful of figures worth keeping forever."""
    totals = dump.get("totals") or {}
    mature = [row for row in rows if row["mature"]]
    retentions = [float(row["retention"]) for row in mature if row.get("retention") is not None]
    return {
        "at": dump.get("collected_at") or datetime.now(UTC).isoformat(timespec="seconds"),
        "subscribers": int(totals.get("subscribers") or 0),
        "views": int(totals.get("views") or 0),
        "videos": int(totals.get("videos") or 0),
        "watch_hours": gates["watch_hours"]["value"],
        "shorts_views_90d": gates["shorts_views"]["value"],
        "mature_count": len(mature),
        "median_views": round(median([row["views"] for row in mature]), 1) if mature else None,
        "median_retention": round(median(retentions), 1) if retentions else None,
        "dead_count": sum(1 for row in mature if row["dead"]),
        "hit_count": sum(1 for row in mature if row["hit"]),
    }


def load_history(root: Path | None = None) -> list[dict]:
    path = (root or find_project_root()) / METRICS_FILE
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text("utf-8"))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def save_snapshot(entry: dict, root: Path | None = None) -> Path:
    """Append this measurement to the permanent record.

    Without this every report rebuilds its own baseline from whatever was
    remembered, which is how a "median of 6,017" ends up quoted for weeks with
    nothing behind it.
    """
    path = (root or find_project_root()) / METRICS_FILE
    history = load_history(root)
    history.append(entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(history, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def load_studio(root: Path | None = None) -> list[dict]:
    """Impressions and CTR, if anybody has typed them in from Studio."""
    path = (root or find_project_root()) / STUDIO_FILE
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text("utf-8"))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []
