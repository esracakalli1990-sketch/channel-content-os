"""idea_version 8509dc8a vs earlier sets, age-matched. Read-only."""
import json, sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from statistics import median, mean
from zoneinfo import ZoneInfo
sys.path.insert(0, "/home/user/channel-content-os/src")
from channel_ops.channel_analysis import mann_whitney_p

log, pub = sys.argv[1:3]
PT = ZoneInfo("America/Los_Angeles")
sec, cur = {}, None
for l in open(log).read().split("\n"):
    if l.startswith("### "): cur = l[4:].strip(); sec[cur] = []
    elif cur and l.strip(): sec[cur].append(l.split("\t"))
h = sec["VIDEOLAR"][0]; V = {r[h.index("id")]: dict(zip(h, r)) for r in sec["VIDEOLAR"][1:]}
ser = defaultdict(dict)
for r in sec["HACIM_VIDEO_GUNLUK"][1:]:
    if len(r) == 4 and not r[0].startswith("HATA"): ser[r[0]][date.fromisoformat(r[1])] = (int(r[2]), int(r[3]))
ch = {date.fromisoformat(r[0]): (int(r[1]), int(r[2])) for r in sec["HACIM_KANAL_GUNLUK"][1:] if len(r) == 3 and r[0][:2] == "20"}
last = max(d for d, (v, _) in ch.items() if v > 0)
NOW = datetime.now().astimezone()
EARLY = date(2026, 10, 27)
seen, out, skip = set(), [], defaultdict(int)
for r in sorted(json.load(open(pub)), key=lambda r: r["published_at"]):
    vid = r.get("youtube_video_id"); t = datetime.fromisoformat(r["published_at"].replace("Z", "+00:00"))
    fid = r.get("file_id")
    if t.date() < date(2026, 10, 1): seen.add(fid); continue
    if fid and fid in seen: skip["kopya"] += 1; continue
    seen.add(fid)
    v = V.get(vid, {})
    if r.get("youtube_deleted") or v.get("gorunurluk", "public") != "public": skip["gizli/silinmiş"] += 1; continue
    if v.get("boyut") == "1280x720": skip["yatay"] += 1; continue
    if t > NOW - timedelta(days=5): skip["5 günden genç"] += 1; continue
    iv = (r.get("idea_version") or "")[:8]
    grp = "yeni" if iv == "8509dc8a" else "eski" if iv in ("18ae880d", "10497d19") and t.date() <= date(2026, 11, 10) else None
    if not grp: skip[f"diğer sürüm {iv or '-'}"] += 1; continue
    pd = t.astimezone(PT).date(); s = ser.get(vid, {})

    def thr(k, i, s=s, pd=pd):
        if pd + timedelta(days=k) > last or not s: return None
        return sum(s.get(pd + timedelta(days=j), (0, 0))[i] for j in range(k + 1))
    def num(x, f=float):
        try: return f(x)
        except (TypeError, ValueError): return None
    out.append(dict(id=vid, t=t, cre=r.get("creature", ""), grp=grp, v3=thr(3, 0), e3=thr(3, 1),
                    live=num(v.get("izlenme"), int), avp=num(v.get("izl%")), mat=(r.get("material") or "")[:34]))

print(f"Analytics son gün {last} | dışarıda: {dict(skip)}")
for g in ("eski", "yeni"):
    xs = [x for x in out if x["grp"] == g]
    print(f"{g}: n={len(xs)} {min(x['t'] for x in xs):%m-%d}–{max(x['t'] for x in xs):%m-%d}, gün+3 olgun {sum(x['e3'] is not None for x in xs)}")
recent = lambda x: x["grp"] == "yeni" or x["t"].date() >= EARLY


def cmp(label, key, sel=lambda x: True):
    a = [x[key] for x in out if x["grp"] == "yeni" and x[key] is not None and sel(x)]
    b = [x[key] for x in out if x["grp"] == "eski" and x[key] is not None and sel(x)]
    if not a or not b: print(label, "veri yok"); return
    p = mann_whitney_p(a, b)
    verdict = "yeterli veri yok" if min(len(a), len(b)) < 8 else ("anlamlı" if p < .05 else "fark gösterilemedi")
    print(f"{label:46} yeni n={len(a):3} med={median(a):8.0f} | eski n={len(b):3} med={median(b):8.0f} | oran {median(a)/max(1,median(b)):.2f} p={p:.3f} {verdict}")


print()
cmp("gün+3 etkileşimli (eski: 1 Eki–10 Kas)", "e3")
cmp("gün+3 etkileşimli (eski: 27 Eki–10 Kas)", "e3", recent)
cmp("gün+3 izlenme (eski: 27 Eki–10 Kas)", "v3", recent)
cmp("ortalama görüntüleme % (eski: 27 Eki–10 Kas)", "avp", recent)
for g in ("eski", "yeni"):
    xs = [x for x in out if x["grp"] == g and x["v3"] is not None and recent(x)]
    print(f"{g} (27 Eki+): gün+3 ≥10K {sum(x['v3'] >= 10000 for x in xs)}/{len(xs)} | ≥50K {sum(x['v3'] >= 50000 for x in xs)} | <2K {sum(x['v3'] < 2000 for x in xs)}")


def bg(d0, d1):
    ds = [d for d in ch if d0 <= d <= d1]
    return mean(ch[d][1] for d in ds)


print(f"\nkanal etkileşimli/gün: 27 Eki–10 Kas {bg(EARLY, date(2026, 11, 10)):.0f} | 11 Kas–{last} {bg(date(2026, 11, 11), last):.0f}")
print("\n== yeni set videoları")
for x in sorted([x for x in out if x["grp"] == "yeni"], key=lambda x: x["t"]):
    print(f"{x['t']:%m-%d %H} {x['cre'][:22]:22} gün+3 {x['v3'] if x['v3'] is not None else '-':>7} canlı {x['live']} izl% {x['avp']} | {x['mat']}")
