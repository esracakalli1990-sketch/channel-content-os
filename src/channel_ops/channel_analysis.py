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

# The hook proxy (engagedViews / views) comes from Analytics, which runs two to
# three days behind the Data API's live view count. For a video younger than
# this the engaged figure has not caught up, so the ratio would measure how
# recently it was published, not how well it held people.
ENGAGED_MIN_AGE_HOURS = 120

# Template hand-over test: the last K videos of one template against the first
# K of the next. K is fixed in advance so it cannot be shopped for; the others
# are printed as sensitivity checks and do not enter the Holm family.
TRANSITION_K = 10
TRANSITION_SENSITIVITY_K = (8, 12, 15)
# A time trend inside one template needs more than a handful of points.
TREND_MIN = 12

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


# Verdicts that assert a difference or a trend; Holm withdraws them if the
# corrected p no longer supports it.
_CLAIMS = ("daha iyi", "yükseliyor", "düşüyor")


def apply_holm(results: list[dict]) -> list[dict]:
    """Correct a family of comparisons for having asked many questions.

    Each report runs about sixteen rank tests. At p<0.05 one of them comes
    back "significant" on noise alone roughly every other report, and on
    28 September one did: "hitler daha yüksek tutundurur" passed at p=0.049,
    then failed the following week. A threshold meant for one question cannot
    be reused sixteen times without being tightened.

    Holm-Bonferroni rather than plain Bonferroni: it is just as safe against
    false positives and throws away less power, which matters when a real
    effect here is worth acting on.

    Both p-values are kept. The raw one says what the data did; the adjusted
    one says whether to believe it. Dropping the raw value would hide how
    close a call was.
    """
    testable = [r for r in results if r.get("p") is not None]
    for rank, result in enumerate(sorted(testable, key=lambda r: r["p"])):
        # Holm: the k-th smallest p is compared against alpha/(m-k).
        adjusted = min(1.0, result["p"] * (len(testable) - rank))
        result["p_adjusted"] = round(adjusted, 4)
        result["tests_in_family"] = len(testable)
        if adjusted >= SIGNIFICANCE and any(
                claim in result.get("verdict", "") for claim in _CLAIMS):
            result["verdict"] = (
                f"fark yok (ham p={result['p']:.3f} ama {len(testable)} testin "
                f"arasında düzeltilince p={adjusted:.3f})"
            )
        elif adjusted < SIGNIFICANCE:
            result["verdict"] += f" · düzeltilmiş p={adjusted:.3f}"
    return results


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
    engaged = _engaged_by_video(dump)
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
        # A published record the Data API no longer returns is a video that was
        # deleted. It used to be read as 0 views and counted as a dead video in
        # every report since it vanished (#9, 7Car9xssqew). Only judged once
        # the video is old enough that "not returned" cannot mean "not indexed
        # yet", and only when the API answered for other videos at all -- an
        # empty reply means the call failed, not that everything was deleted.
        deleted = bool(stats) and vid not in stats and age >= MATURITY_HOURS
        hook = None
        if (engaged and vid in engaged and views and not deleted
                and age >= ENGAGED_MIN_AGE_HOURS):
            hook = round(engaged[vid] / views * 100, 1)
        out.append({
            "id": vid,
            "creature": creature,
            "published_at": published_at,
            "hour": published_at[11:13] if len(published_at) >= 13 else "",
            "weekday": _weekday(published_at),
            "age_hours": round(age, 1),
            "deleted": deleted,
            "mature": age >= MATURITY_HOURS and not deleted,
            "views": views,
            "seconds": data.get("seconds", 0),
            "retention": row.get("averageViewPercentage"),
            "watch_seconds": row.get("averageViewDuration"),
            "subs_per_1k": _per_thousand(row.get("subscribersGained"), views),
            "likes_per_1k": _per_thousand(data.get("likes"), views),
            "comments_per_1k": _per_thousand(data.get("comments"), views),
            "shares_per_1k": _per_thousand(row.get("shares"), views),
            # PROXY, not Studio's "chose to view / swiped away": views counts
            # replays, so the two diverge as loops rise. Useful for ranking
            # videos against each other, not as an equivalent of that number.
            "engaged_ratio": hook,
            "template": (record.get("template_version") or "-")[:8],
            "idea": (record.get("idea_version") or "-")[:8],
            "kind": "makine" if _is_machine(creature) else "canli",
            # Grouped by the metal rather than the exact phrase: "patinated
            # bronze plates" and "antiqued bronze" are the same answer to the
            # question being asked, and left as written every video would be
            # its own group and nothing could ever be compared.
            "metal": _metal(record.get("material", "") or record.get("title", "")),
            "dead": age >= MATURITY_HOURS and not deleted and views < DEAD_VIEWS,
            "hit": views >= HIT_VIEWS and not deleted,
        })
    return out


def settle_deleted(rows: list[dict]) -> list[dict]:
    """Apply the deleted-video rule to rows exported before the rule existed.

    ``data/expert_dataset.json`` was written when a vanished video came out as
    0 views and no duration. Such a row is a deleted video; leaving it as a
    mature dead one would put the old error back into every document typeset
    from that file.
    """
    for row in rows:
        if "deleted" not in row:
            row["deleted"] = not row.get("views") and not row.get("seconds")
        if row["deleted"]:
            row["mature"] = row["dead"] = row["hit"] = False
    return rows


def _weekday(published_at: str) -> str:
    try:
        stamp = datetime.fromisoformat(published_at)
    except (TypeError, ValueError):
        return ""
    return ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"][stamp.weekday()]


def _is_machine(creature: str) -> bool:
    return any(word in creature for word in _MACHINE_WORDS)


# The materials viewers actually type into search. Anything else is "diğer"
# rather than its own group, so the comparison stays answerable.
_METALS = ("bronze", "brass", "copper", "titanium", "steel", "pewter",
           "gunmetal", "ceramic", "obsidian", "aluminium", "aluminum")


def _metal(material: str) -> str:
    """The metal in a material phrase -- or, for older records, in the title.

    Nothing published before today carries the material field, and waiting
    months for enough new records would mean the question could not be asked
    until long after it stopped being interesting. The title is written from
    the same concept and names the material almost every time: "Why does this
    wedge of brass have a hidden hinge?". Reading it back is a fallback, not a
    second source -- when both exist the material field wins.
    """
    for metal in _METALS:
        if metal in material.lower():
            return metal
    return "diğer"


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

    # WHICH NUMBER THE SHORTS GATE IS COUNTED IN. Since March 2025 a Shorts
    # "view" counts every play and every replay, but the Partner Programme
    # thresholds count ENGAGED views -- the older, stricter measure. On this
    # channel the two differ by more than half: 2,259,715 views against
    # 1,012,547 engaged over the same 88 days, a ratio of 44.8%. Studio's own
    # eligibility page agrees, showing 795K eligible against the 3M target on
    # 23 September while raw views stood near three million.
    #
    # Counting the gate in raw views therefore reported it as PASSED when it
    # stood at about a third. That was told to the operator as fact. Views are
    # the right number for judging whether a video did well and the wrong one
    # for judging distance to monetisation, and nothing else in this file cares
    # about the difference -- only this function does.
    engaged = _engaged_by_video(dump)
    short_views_90d = 0
    long_minutes_90d = 0.0
    for vid, row in live.items():
        data = stats.get(vid) or {}
        seconds = data.get("seconds", 0)
        if vid in long_ids or seconds > SHORT_MAX_SECONDS:
            # Watch time has no Data API equivalent, so this stays on Analytics
            # whatever else changes.
            long_minutes_90d += float(row.get("estimatedMinutesWatched") or 0)
        elif engaged:
            short_views_90d += int(engaged.get(vid, 0))
        else:
            # No engaged figures in this dump. Rather than quietly substitute a
            # number that runs about twice too high, the gate is still filled
            # in but flagged, so the report says it cannot be trusted instead
            # of stating a wrong threshold with a confident bar next to it.
            short_views_90d += int(data.get("views") or 0)

    subscribers = int(totals.get("subscribers") or 0)
    watch_hours = long_minutes_90d / 60

    measure = "engaged" if engaged else "views"
    uploads_90d = _recent_uploads(dump)
    # Videos Analytics has not reported on at all are missing from both reports,
    # and they are the newest ones -- exactly the ones carrying a surge. Their
    # raw view counts are NOT added in: raw and engaged views are different
    # measures and summing them would produce a number that is neither. The
    # total therefore runs slightly low for a day or two after a video lands,
    # which the note below says out loud. Undercounting a threshold is the
    # safe direction; overcounting one is how it got reported as passed.
    lag = _analytics_edge(dump, datetime.now(UTC))[1]

    return {
        # The watch-hour goals are counted over twelve months and this window is
        # ninety days. The channel is younger than ninety days, so the two are
        # the same number today; the day that stops being true this figure
        # starts understating and the note has to change with it.
        "window_note": "izlenme saati 90 günlük pencereden; kanal 90 günden genç olduğu sürece = 12 ay",
        "shorts_engaged": bool(engaged),
        "shorts_floor_note": (
            "Shorts sayısı ETKİLEŞİMLİ görüntülemeden (engagedViews); eşiğin "
            f"saydığı rakam bu. Analytics {lag} gün geriden geliyor, yani "
            "gerçek rakam biraz daha yüksek."
            if engaged
            else "⚠️ ETKİLEŞİMLİ GÖRÜNTÜLEME YOK — aşağıdaki Shorts rakamı ham "
                 "görüntüleme ve eşiğin saydığı sayı DEĞİL. Bu kanalda ham "
                 "görüntüleme etkileşimlinin ~2,2 katı, yani eşiğe kalan mesafe "
                 "olduğundan çok daha yakın görünüyor. Güvenme."
        ),
        "uploads_90d": uploads_90d,
        # Lower tier first: it is the one that is actually near, and a
        # scoreboard that leads with the unreachable number is a scoreboard
        # nobody reads twice.
        "early_subscribers": _gate("Abone (alt kademe)", subscribers,
                                   EARLY_SUBSCRIBER_GOAL, history, "subscribers"),
        "early_watch_hours": _gate("İzlenme saati (alt kademe)", round(watch_hours, 1),
                                   EARLY_WATCH_HOUR_GOAL, history, "watch_hours"),
        "early_shorts_views": _gate(
            f"{SHORTS_VIEW_WINDOW_DAYS} günlük etkileşimli Shorts (alt kademe)"
            if engaged
            else f"{SHORTS_VIEW_WINDOW_DAYS} günlük Shorts ⚠️ham (alt kademe)",
                                    short_views_90d, EARLY_SHORTS_VIEW_GOAL,
                                    history, "shorts_views_90d",
                                    measure=measure),
        "subscribers": _gate("Abone", subscribers, SUBSCRIBER_GOAL, history, "subscribers"),
        "watch_hours": _gate("İzlenme saati (uzun video)", round(watch_hours, 1),
                             WATCH_HOUR_GOAL, history, "watch_hours"),
        "shorts_views": _gate(
            f"{SHORTS_VIEW_WINDOW_DAYS} günlük etkileşimli Shorts"
            if engaged
            else f"{SHORTS_VIEW_WINDOW_DAYS} günlük Shorts ⚠️ham",
                              short_views_90d, SHORTS_VIEW_GOAL, history,
                              "shorts_views_90d", measure=measure),
    }


def _engaged_by_video(dump: dict) -> dict[str, int]:
    """Engaged views per video, or {} when the report is not in this dump.

    Empty is the honest answer for "not measured" -- a caller that treats it
    as zero would report every threshold as untouched, which is at least
    obviously wrong rather than quietly wrong.
    """
    block = (dump.get("analytics") or {}).get("engaged_per_video") or {}
    columns = block.get("columns") or []
    if not block.get("rows") or "engagedViews" not in columns:
        return {}
    video_at = columns.index("video")
    engaged_at = columns.index("engagedViews")
    return {
        row[video_at]: int(row[engaged_at] or 0)
        for row in block["rows"]
        if row[video_at]
    }


def _oldest_publish(dump: dict) -> datetime | None:
    """When the channel's first video went out, or None if nothing has."""
    stamps = []
    for record in (dump.get("published") or []) + (dump.get("long_form") or []):
        try:
            when = datetime.fromisoformat(record.get("published_at", ""))
        except (TypeError, ValueError):
            continue
        stamps.append(when if when.tzinfo else when.replace(tzinfo=UTC))
    return min(stamps) if stamps else None


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


def _gate(name: str, value: float, goal: float, history: list[dict], key: str,
          now: datetime | None = None, measure: str | None = None) -> dict:
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
    now = now or datetime.now(UTC)
    rate = _rate_per_day(history, key, value, now, measure)
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
    gate["eta"] = (now + timedelta(days=days)).date().isoformat()
    gate["note"] = f"ölçülen hızla {round(days)} gün"
    return gate


def suspect_snapshot(snapshot: dict) -> bool:
    """A recorded 90-day figure larger than the channel's lifetime total.

    Impossible by construction: a part cannot exceed the whole. It happened
    once (28 September 14:12, 3,108,912 against 2,618,818) and its cause is
    unknown, so the row is kept -- the record is append-only -- but flagged,
    and kept out of every rate.
    """
    try:
        return float(snapshot["shorts_views_90d"]) > float(snapshot["views"])
    except (KeyError, TypeError, ValueError):
        return False


def _rate_per_day(history: list[dict], key: str, current: float,
                  now: datetime | None = None,
                  measure: str | None = None) -> float | None:
    """Growth per day, measured against the oldest snapshot inside two weeks.

    Against the oldest rather than the previous one because a single viral
    video makes any two-day rate meaningless: on 21 September the channel was
    briefly running at 140,000 views a day, which extrapolated to passing a
    threshold that it is nowhere near.
    """
    now = now or datetime.now(UTC)
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
        if key == "shorts_views_90d" and suspect_snapshot(snapshot):
            continue
        # Comparing a figure counted one way against the same figure counted
        # another way produces a rate that describes the change of ruler, not
        # the channel. Rows from before the measure was recorded carry no label
        # and are skipped too: unlabelled means unknown, not "same as now".
        if measure is not None and snapshot.get("shorts_measure") != measure:
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

    Only the dates BOTH groups were publishing in. A template is used for a
    stretch of days and then replaced, so its group is a slice of time as much
    as a slice of content, and this channel went from six thousand views a day
    to four hundred thousand inside three weeks: against that, any later group
    wins whatever it contains. Printing such a comparison with a warning beside
    it (as this used to) still printed a p-value, and a p-value gets quoted
    without its warning. When the two groups never ran side by side there is no
    comparison to make and none is made.
    """
    mature = [row for row in rows if row["mature"] and row.get(metric) is not None]
    out = []
    for name, key in (("Şablon", "template"), ("Fikir talimatı", "idea"),
                      ("Yayın saati", "hour"), ("Konu türü", "kind"),
                      ("Malzeme", "metal")):
        groups: dict[str, int] = {}
        for row in mature:
            groups[row[key]] = groups.get(row[key], 0) + 1
        ranked = sorted(groups.items(), key=lambda item: -item[1])[:2]
        if len(ranked) < 2:
            continue
        (label_a, full_a), (label_b, full_b) = ranked

        window = common_window(mature, key, label_a, label_b)
        if window is None:
            out.append({
                "a": label_a, "b": label_b, "n_a": 0, "n_b": 0,
                "n_before": (full_a, full_b), "median_a": None, "median_b": None,
                "p": None, "dimension": name, "metric": metric,
                "verdict": "yeterli ortak dönem yok (iki grup hiç aynı günlerde yayınlanmadı)",
            })
            continue
        first, last = window
        inside = [row for row in mature
                  if first <= row["published_at"][:10] <= last]
        values_a = [float(r[metric]) for r in inside if r[key] == label_a]
        values_b = [float(r[metric]) for r in inside if r[key] == label_b]
        result = compare(label_a, values_a, label_b, values_b)
        result["dimension"] = name
        result["metric"] = metric
        result["window"] = f"{first} – {last}"
        result["n_before"] = (full_a, full_b)
        if result["p"] is None:
            result["verdict"] = (
                f"yeterli ortak dönem yok (ortak aralık {first} – {last}: "
                f"n={len(values_a)}+{len(values_b)}, en az {MIN_GROUP}+{MIN_GROUP} gerekiyor)"
            )
        if key == "kind":
            result["method"] = "tür ayrımı anahtar kelimeyle yapıldı, YouTube vermiyor"
        out.append(result)
    return out


def common_window(rows: list[dict], key: str, label_a: str, label_b: str):
    """The dates both groups were publishing in, or None if they never did.

    Overlapping dates mean the two were running side by side and the
    comparison is about them. Disjoint dates mean it would also be about
    everything else that changed in between.
    """
    def span(label):
        dates = sorted(
            row["published_at"][:10] for row in rows
            if row[key] == label and row.get("published_at")
        )
        return (dates[0], dates[-1]) if dates else None

    first, second = span(label_a), span(label_b)
    if not first or not second:
        return None
    start, end = max(first[0], second[0]), min(first[1], second[1])
    return (start, end) if start <= end else None


# -----------------------------------------------------------------------
# Does a template change do anything, or was the climb already under way?
# -----------------------------------------------------------------------

def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the incomplete beta function (Lentz's method)."""
    tiny = 1e-30
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 200):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 3e-12:
            break
    return h


def _betai(a: float, b: float, x: float) -> float:
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
                     + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(x: list[float], y: list[float]) -> tuple[float, float] | None:
    """Rank correlation and its two-sided p-value (t distribution, exact tail).

    None when it cannot be computed. Written out rather than imported because
    the repository's analysis has no third-party dependencies, and a p-value
    from an approximation that is only good for large n would be a quiet way
    to repeat the failure this module exists to prevent.
    """
    n = len(x)
    if n != len(y) or n < 4:
        return None
    rx, ry = _ranks(x), _ranks(y)
    mx, my = sum(rx) / n, sum(ry) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sxx = sum((a - mx) ** 2 for a in rx)
    syy = sum((b - my) ** 2 for b in ry)
    if sxx == 0 or syy == 0:
        return None
    rho = sxy / math.sqrt(sxx * syy)
    if abs(rho) >= 1.0:
        return rho, 0.0
    df = n - 2
    t = rho * math.sqrt(df / (1.0 - rho * rho))
    return rho, _betai(df / 2.0, 0.5, df / (df + t * t))


def _by_template(rows: list[dict]) -> tuple[list[str], dict[str, list[dict]]]:
    mature = sorted((r for r in rows if r["mature"] and r.get("published_at")),
                    key=lambda r: r["published_at"])
    grouped: dict[str, list[dict]] = {}
    for row in mature:
        grouped.setdefault(row["template"], []).append(row)
    order = sorted(grouped, key=lambda t: grouped[t][0]["published_at"])
    return order, grouped


def template_transitions(rows: list[dict]) -> list[dict]:
    """For each hand-over: the last K videos of the old template against the
    first K of the new one.

    If the template itself did something, the videos either side of the switch
    should differ. If the climb was already under way, they will look alike.
    ``primary`` (K fixed at TRANSITION_K) is the one test that enters the
    report's Holm family; the others are sensitivity checks.
    """
    order, grouped = _by_template(rows)
    out = []
    for old, new in zip(order, order[1:]):
        old_views = [float(r["views"]) for r in grouped[old]]
        new_views = [float(r["views"]) for r in grouped[new]]
        entry = {"old": old, "new": new, "primary": None, "windows": []}
        for k in (TRANSITION_K, *TRANSITION_SENSITIVITY_K):
            if len(old_views) < k or len(new_views) < k:
                continue
            tail, head = old_views[-k:], new_views[:k]
            result = compare(f"{old} son {k}", tail, f"{new} ilk {k}", head)
            result["k"] = k
            result["dimension"] = f"Şablon geçişi {old} → {new}"
            if k == TRANSITION_K:
                entry["primary"] = result
            else:
                entry["windows"].append(result)
        out.append(entry)
    return out


def template_trends(rows: list[dict]) -> list[dict]:
    """Views against publishing order inside each template.

    A template that climbs while it is still in use was not what made it
    climb, or not all of it. Only templates with TREND_MIN+ videos are tested.
    """
    order, grouped = _by_template(rows)
    out = []
    for name in order:
        views = [float(r["views"]) for r in grouped[name]]
        result = {"template": name, "n": len(views), "p": None,
                  "dimension": f"Şablon içi trend {name}"}
        if len(views) < TREND_MIN:
            result["verdict"] = f"yeterli veri yok (en az {TREND_MIN} gerekiyor)"
            out.append(result)
            continue
        got = spearman([float(i) for i in range(len(views))], views)
        if got is None:
            result["verdict"] = "hesaplanamadı"
            out.append(result)
            continue
        rho, p = got
        result["rho"] = round(rho, 3)
        result["p"] = round(p, 4)
        if p >= SIGNIFICANCE:
            result["verdict"] = f"trend yok (rho={rho:.2f})"
        else:
            word = "yükseliyor" if rho > 0 else "düşüyor"
            result["verdict"] = f"zamanla {word} (rho={rho:.2f}, p={p:.3f})"
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
                      ("Yayın saati", "hour"), ("Gün", "weekday"),
                      ("Malzeme", "metal")):
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
# Is the data actually current?
# -----------------------------------------------------------------------

# A channel taking ten thousand views an hour cannot report a bit-identical
# view count an hour later. When it does, the API is serving a cached answer.
FROZEN_AFTER_MINUTES = 30


def staleness(dump: dict, history: list[dict], now: datetime | None = None) -> dict:
    """Whether these numbers moved since last time, and how far Analytics lags.

    Added after a report presented an hour-old cached response as current:
    subscribers, views, median and hit count were all identical to the previous
    run while Studio showed roughly ten thousand views an hour arriving. The
    report was not wrong about what the API said; it was wrong to present it
    without saying the API had stopped answering. A measurement system that
    cannot notice it has gone blind is worse than no measurement system,
    because it is trusted.
    """
    now = now or datetime.now(UTC)
    result: dict = {"frozen": False, "warnings": []}

    totals = dump.get("totals") or {}
    current_views = int(totals.get("views") or 0)
    previous = history[-1] if history else None
    if previous and previous.get("views") is not None:
        try:
            when = datetime.fromisoformat(previous["at"])
        except (KeyError, ValueError):
            when = None
        if when is not None:
            if when.tzinfo is None:
                when = when.replace(tzinfo=UTC)
            minutes = (now - when).total_seconds() / 60
            if (int(previous["views"]) == current_views
                    and minutes >= FROZEN_AFTER_MINUTES):
                result["frozen"] = True
                result["frozen_minutes"] = round(minutes)
                result["warnings"].append(
                    f"Data API DONMUŞ: toplam izlenme {minutes:.0f} dakikadır "
                    f"birebir aynı ({current_views}). Bu rapordaki gerçek zamanlı "
                    "sayılar güvenilmez; Studio'ya bak."
                )

    last_day, lag = _analytics_edge(dump, now)
    result["analytics_last_day"] = last_day
    result["analytics_lag_days"] = lag
    if lag is not None and lag > 3:
        result["warnings"].append(
            f"Analytics {lag} gün geriden geliyor (son dolu gün {last_day}). "
            "İzlenme yüzdesi ve trafik kaynağı bu tarihe kadar; son günlerin "
            "sıfır görünmesi düşüş değil, veri yokluğu."
        )
    return result


def _analytics_edge(dump: dict, now: datetime) -> tuple[str | None, int | None]:
    """The most recent day Analytics has any views for, and how stale that is.

    Trailing zero days are skipped rather than treated as the edge: a genuine
    zero-view day and a not-yet-reported day look identical in this table, and
    once already a run of unreported days was read as a collapse in views.
    """
    block = (dump.get("analytics") or {}).get("daily") or {}
    columns = block.get("columns") or []
    if "day" not in columns:
        return None, None
    day_at = columns.index("day")
    views_at = columns.index("views") if "views" in columns else None
    last = None
    for row in block.get("rows") or []:
        if views_at is not None and not row[views_at]:
            continue
        last = row[day_at]
    if not last:
        return None, None
    try:
        when = datetime.fromisoformat(str(last)).replace(tzinfo=UTC)
    except ValueError:
        return last, None
    return last, (now.date() - when.date()).days


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
        # WHICH MEASURE shorts_views_90d holds. On 28 September the gate moved
        # from raw views to engaged views and the recorded figure dropped from
        # 3,108,912 to 1,012,415 -- same channel, different ruler. The rate
        # calculation then compared the two and concluded the threshold was
        # going backwards, printing "artmıyor — bu hızla ulaşılamaz" on a
        # figure that had risen 64% in four days. Any later change of measure
        # would do the same unless each row says which one it used.
        "shorts_measure": "engaged" if gates.get("shorts_engaged") else "views",
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
