#!/usr/bin/env bash
# Watch Telegram for finished videos, continuously, for most of a run.
#
# The job used to be a single check on a */15 cron. GitHub does not honour that
# on this repository: the runs arrived 2-3 hours apart, so a video sent just
# after one check waited hours for the next. Instead of asking for a schedule
# GitHub will not keep, one run now stays alive and polls. Runs are scheduled
# far enough apart that a delayed start still overlaps the previous run, and
# the concurrency group queues rather than drops them, so coverage stays
# continuous even when GitHub fires late.
#
# Nothing here may exit on a single bad poll. Ending the watch early would put
# the next video back on the mercy of the schedule.
set -uo pipefail

POLL_SECONDS="${POLL_SECONDS:-120}"
# Stops well inside the six-hour ceiling GitHub kills a job at.
RUN_SECONDS="${RUN_SECONDS:-19800}" # 5h30m
BRANCH="${GITHUB_REF_NAME:-main}"

git config user.name  "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

notify() {
    curl -sS -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        -d chat_id="${TELEGRAM_CHAT_ID}" \
        -d parse_mode=HTML \
        --data-urlencode "text=$1" > /dev/null || true
}

# The reading cursor and the publish record live in the repository, so they
# have to be committed as they change rather than at the end: if the job is
# cancelled with an uncommitted cursor, Telegram replays the video and it is
# published twice.
save_state() {
    # Everything under data/ rather than a list of names. The list silently
    # omitted shorts_queue.json when the release queue was added, so every
    # queued video was wiped by the next refresh and three clips were lost.
    # A new state file must not be able to go missing the same way.
    git add data/
    if git diff --cached --quiet; then
        return 0
    fi
    git commit -q -m "chore: telegram imleci ve yayın kaydını güncelle"
    git pull -q --rebase --autostash origin "$BRANCH" && git push -q origin "HEAD:$BRANCH"
}

# A run lasts five and a half hours, but the checkout is from the second it
# started. Anything pushed in between — a corrected concept, a fixed bug — was
# invisible to it, and an idea restored to the pool mid-run would have been
# missed entirely, matching the arriving video to the wrong concept and
# publishing it under the wrong title. Refresh before every poll.
refresh() {
    git fetch -q origin "$BRANCH" || return 1
    # Rebase, not reset --hard. If save_state's push lost a race its commit is
    # still only local, and resetting would throw away a queued video along
    # with it. On a conflict the local state is kept and the next round tries
    # again — dropping work is never the safer choice here.
    if ! git rebase -q --autostash "origin/$BRANCH"; then
        git rebase --abort 2>/dev/null || true
        return 1
    fi
}

deadline=$(( SECONDS + RUN_SECONDS ))
failing=0

echo "Telegram dinleniyor: her ${POLL_SECONDS} saniyede bir, ${RUN_SECONDS} saniye boyunca."

while (( SECONDS < deadline )); do
    # Hard reset is safe here: everything this job produces is committed by
    # save_state at the end of the previous iteration, so there is never work
    # in the tree worth keeping.
    refresh || echo "Depo tazelenemedi; bu tur onceki kopyayla calisiyor."

    if python -m channel_ops shorts-inbox; then
        if (( failing )); then
            notify "✅ Gelen kutusu tekrar çalışıyor."
            failing=0
        fi
    elif (( failing == 0 )); then
        # Reported once per outage. A message every two minutes would bury the
        # chat the prompts and publish notices arrive in.
        notify "⚠️ Gelen kutusu hata verdi — ${RUN_URL:-GitHub Actions}"
        failing=1
    fi

    save_state || echo "Durum kaydedilemedi; sonraki turda tekrar denenecek."
    sleep "$POLL_SECONDS"
done

save_state || true
echo "Tur tamamlandı."
