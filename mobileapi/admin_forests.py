"""Admin → Forests: every user's Forest (Tools → Forest in the app), drawn the way their phone draws it,
and trees the admin can give any user. Trees and calm minutes come from what the user's phones synced
(signed in, Settings → Sync → Forest on); given trees are synced items like a phone's, so the phones
get them on their next sync. Superuser-only, like the rest of /mobile/.
"""
import time
from datetime import timedelta

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from . import forest_art as art
from . import forests as fx
from .admin_views import superuser_required
from .models import AppAccount, SyncItem

STAGE_ORDER = {s: i for i, s in enumerate(fx.STAGES)}


def _identifier(a):
    return a.email or a.phone or (f"@{a.username}" if a.username else "")


def _ago(day_n, today):
    if day_n is None:
        return "No calm time yet"
    days = (today - fx.int_day(day_n)).days
    return "Calm time today" if days <= 0 else "Calm time yesterday" if days == 1 else f"Last calm time {days} days ago"


def _season_month(species):
    """A month when the kind shows its flowers (or fruit), for its picture in the list of kinds."""
    months = sorted(art.SEASONS[species])
    flowering = [m for m in months if not (species in fx.FRUIT or (species == "MANGO" and m >= 4))]
    return (flowering or months or [timezone.localtime().month])[0]


# ------------------------------------------------------------------ all forests
@superuser_required
def forests(request):
    q = (request.GET.get("q") or "").strip()
    show = "all" if request.GET.get("show") == "all" else "forest"
    now = timezone.localtime()
    now_ms = int(time.time() * 1000)
    today = now.date()

    accounts = AppAccount.objects.all()
    if q:
        accounts = accounts.filter(Q(name__icontains=q) | Q(email__icontains=q) | Q(phone__icontains=q) | Q(username__icontains=q))
    accounts = accounts.annotate(
        n_trees=Count("sync_items", filter=Q(sync_items__kind="tree", sync_items__deleted=False)),
        n_calm=Count("sync_items", filter=Q(sync_items__kind="calmlog", sync_items__deleted=False)),
    )
    if show == "forest":
        accounts = accounts.filter(Q(n_trees__gt=0) | Q(n_calm__gt=0))
    page = Paginator(accounts.order_by("-n_trees", "-n_calm", "name"), 24).get_page(request.GET.get("page"))

    loaded = fx.load_many(list(page.object_list))
    cards = []
    for a in page.object_list:
        f = loaded[a.pk]
        resting = f.resting(today)
        stages = [(t, t.stage(now_ms)) for t in f.trees]
        best = sorted(stages, key=lambda ts: (-STAGE_ORDER[ts[1]], ts[0].planted_ms))[:6]
        grown = sum(1 for _, s in stages if s == "GROWN")
        cards.append({
            "account": a,
            "identifier": _identifier(a),
            "trees": len(f.trees),
            "grown": grown,
            "growing": len(f.trees) - grown,
            "given": sum(1 for t in f.trees if t.by_admin),
            "minutes": fx.minutes_text(f.total_minutes),
            "calm_days": f.calm_days,
            "streak": f.streak(today),
            "last": _ago(f.last_day, today),
            "resting": resting,
            "thumbs": [{"url": fx.art_url(t.species, s, t.variant, now.month, resting), "stage": s.lower(), "title": t.stage_name(now_ms)} for t, s in best],
        })

    # Totals over everyone.
    week_from = fx.day_int(today - timedelta(days=6))
    all_trees = SyncItem.objects.filter(kind="tree", deleted=False).only("key", "data", "updated_ms", "device_id", "account_id")
    trees = [fx.Tree.from_item(i) for i in all_trees]
    grown_all = sum(1 for t in trees if t.stage(now_ms) == "GROWN")
    minutes_all = minutes_week = 0
    for key, data in SyncItem.objects.filter(kind="calmlog", deleted=False).values_list("key", "data"):
        m = int((data or {}).get("min") or 0)
        minutes_all += m
        try:
            if int(key.split("|")[0]) >= week_from:
                minutes_week += m
        except ValueError:
            pass
    people = (SyncItem.objects.filter(kind__in=["tree", "calmlog"], deleted=False).values("account_id").distinct().count())
    querystring = "&".join(x for x in [f"q={q}" if q else "", "show=all" if show == "all" else ""] if x)
    return render(request, "mobileapi/forests.html", {
        "cards": cards, "page": page, "q": q, "show": show, "querystring": querystring,
        "stats": {
            "people": people, "trees": len(trees), "grown": grown_all, "growing": len(trees) - grown_all,
            "hours": fx.minutes_text(minutes_all), "week": fx.minutes_text(minutes_week),
            "given": sum(1 for t in trees if t.by_admin),
        },
    })


# ------------------------------------------------------------------ one forest
@superuser_required
def forest_detail(request, account_id):
    account = get_object_or_404(AppAccount, pk=account_id)
    sky = request.GET.get("sky") if request.GET.get("sky") in ("day", "night") else "now"
    now = timezone.localtime()
    now_ms = int(time.time() * 1000)
    today = now.date()
    f = fx.load(account)
    scene, info = fx.scene_svg(f, now, sky)
    week, week_total, week_days, best_day = fx.week_svg(f, today)
    resting = info["resting"]

    rows = []
    for t in sorted(f.trees, key=lambda t: -t.planted_ms):
        s = t.stage(now_ms)
        rows.append({
            "tree": t, "stage": s, "stage_title": fx.STAGE_TITLES[s], "name": t.stage_name(now_ms),
            "next": t.next_text(now_ms, now.month, resting), "growth": int(t.growth(now_ms) * 100),
            "thumb": fx.art_url(t.species, s, t.variant, now.month, resting, crop=True),
            "planted": fx.ms_date(t.planted_ms), "grows_in": fx.grow_days_text(t.grow_days),
            "care": fx.minutes_text(t.care),
        })
    kinds = []
    for key, (title, botanical, hint) in fx.SPECIES.items():
        mine = [t for t in f.trees if t.species == key]
        found = f.found(key, now_ms)
        kinds.append({
            "key": key, "title": title, "botanical": botanical, "found": found, "hint": hint,
            "seeds": f.seeds(key, now_ms), "count": len(mine),
            "grown": sum(1 for t in mine if t.stage(now_ms) == "GROWN"),
            "url": fx.art_url(key, "GROWN", 7, _season_month(key)),
        })

    today_min = f.minutes_by_day.get(fx.day_int(today), 0)
    hour = {"day": 10, "night": 21}.get(sky, now.hour)
    greeting = ("Good morning" if 5 <= hour <= 11 else "Good afternoon" if 12 <= hour <= 16
                else "Good evening" if 17 <= hour <= 18 else "A calm evening" if 19 <= hour <= 23 else "A quiet night")
    grown = f.grown_count(now_ms)
    growing = [t for t in f.trees if t.stage(now_ms) != "GROWN"]
    if not f.trees:
        story = "No trees yet - give a seed or a tree to start their forest"
    elif resting:
        story = "Resting - no calm time for a week, so the leaves turned gold"
    else:
        parts = []
        week_ago = now_ms - 7 * fx.DAY_MS
        grew = sum(1 for t in f.trees if int(t.stages.get("GROWN") or 0) > week_ago)
        if grew:
            parts.append(f"{grew} tree{'s' if grew != 1 else ''} grew this week")
        flowering = next((t for t in f.trees if t.stage(now_ms) == "GROWN" and now.month in art.SEASONS[t.species]), None)
        if flowering:
            fruiting = flowering.species in fx.FRUIT or (flowering.species == "MANGO" and now.month >= 4)
            parts.append(f"the {flowering.title.lower()} is {'fruiting' if fruiting else 'in flower'}")
        if not parts:
            parts.append(f"{len(growing)} growing - {grown} grown")
        story = " - ".join(parts)
        story = story[0].upper() + story[1:]
    seeds_total = sum(k["seeds"] for k in kinds)
    return render(request, "mobileapi/forest_detail.html", {
        "account": account, "identifier": _identifier(account), "forest": f, "scene": scene, "info": info, "sky": sky,
        "rows": rows, "kinds": kinds, "week": week, "week_total": fx.minutes_text(week_total), "week_days": week_days,
        "best_day": fx.minutes_text(best_day), "today_min": today_min, "goal": 20, "ring": min(100, today_min * 5),
        "greeting": greeting, "story": story, "grown": grown, "growing": len(growing), "seeds_total": seeds_total,
        "total": fx.minutes_text(f.total_minutes), "calm_days": f.calm_days, "streak": f.streak(today),
        "longest": f.longest_streak, "last": _ago(f.last_day, today), "lotus_hours": f.total_minutes // 60,
        "clock": now.strftime("%H:%M"), "stages": [(s.lower(), fx.STAGE_TITLES[s]) for s in fx.STAGES],
        "packets": [{"key": k, "title": v[0], "url": fx.art_url(k, "GROWN", 7, _season_month(k))} for k, v in fx.SPECIES.items()],
        "stage_pics": {s: fx.art_url("NEEM", s, 21, now.month, crop=True) for s in fx.STAGES},
        "given": sum(1 for t in f.trees if t.by_admin),
        "grow_choices": [(d, fx.grow_days_text(d)) for d in range(1, 15)],
    })


@superuser_required
@require_POST
def forest_add(request, account_id):
    account = get_object_or_404(AppAccount, pk=account_id)
    back = reverse("mobile_forest", args=[account.pk])
    species = (request.POST.get("species") or "").strip().upper()
    stage = (request.POST.get("stage") or "GROWN").strip().upper()
    name = (request.POST.get("name") or "").strip()[:40]
    try:
        grow_days = int(request.POST.get("grow_days") or 7)
        count = int(request.POST.get("count") or 1)
    except ValueError:
        grow_days = count = 0
    if species not in fx.SPECIES or stage not in fx.STAGES:
        messages.error(request, "Pick a kind of tree and a stage.")
        return redirect(back)
    if not 1 <= grow_days <= 14 or not 1 <= count <= 20:
        messages.error(request, "Growing time is 1 to 14 days, and up to 20 trees at a time.")
        return redirect(back)
    live = SyncItem.objects.filter(account=account, kind="tree", deleted=False).count()
    if live + count > fx.MAX_TREES:
        messages.error(request, f"{account.name} already has {live} trees - the most a forest holds is {fx.MAX_TREES}.")
        return redirect(back)
    fx.add_trees(account, species, stage, grow_days, count, name)
    what = fx.SPECIES[species][0].lower() if stage == "GROWN" else f"{fx.SPECIES[species][0].lower()} {fx.STAGE_TITLES[stage].lower()}"
    messages.success(request, f"Gave {account.name} {count} {what}{'s' if count > 1 else ''}. "
                              "Their phones get it on the next sync (signed in, Forest sync on).")
    return redirect(back)


@superuser_required
@require_POST
def forest_remove(request, account_id, key):
    account = get_object_or_404(AppAccount, pk=account_id)
    title = fx.remove_tree(account, key)
    if title:
        messages.success(request, f"Removed {title} - it leaves {account.name}'s phones on their next sync.")
    else:
        messages.error(request, "That tree isn't in this forest any more.")
    return redirect("mobile_forest", account.pk)


# ------------------------------------------------------------------ the drawings
@superuser_required
@require_GET
def forest_art(request, name):
    """One tree as an SVG: SPECIES-STAGE-variant-month-resting[-c] (c = small stages close up)."""
    parts = name.split("-")
    try:
        species, stage, variant, month, rest = parts[0], parts[1], int(parts[2]), int(parts[3]), parts[4] == "1"
    except (IndexError, ValueError):
        raise Http404
    crop = len(parts) > 5 and parts[5] == "c"
    if species not in fx.SPECIES or stage not in fx.STAGES or not 1 <= month <= 12 or not 0 <= variant <= 10_000_000:
        raise Http404
    resp = HttpResponse(art.tree_svg(species, stage, variant, month, rest, crop), content_type="image/svg+xml")
    resp["Cache-Control"] = "private, max-age=604800"
    return resp


@superuser_required
@require_GET
def forest_pond(request):
    resp = HttpResponse(art.pond_svg(), content_type="image/svg+xml")
    resp["Cache-Control"] = "private, max-age=604800"
    return resp
