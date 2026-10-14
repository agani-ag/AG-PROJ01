"""Forest (Tools → Forest in the app) for the admin: an account's trees and calm minutes, read from what
its phones synced, with the app's own rules (forest/ForestModel.kt + ForestStore.kt); the forest scene
as the phone draws it; and trees the admin gives (synced items like a phone's, device_id "admin", so
the user's phones get them on their next sync).
"""
import random
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone as dt_timezone

from django.urls import reverse
from django.utils import timezone

from . import forest_art as art
from .models import SyncItem

DAY_MS = 86_400_000
HOUR_MS = 3_600_000
ADMIN_DEVICE = "admin"
REST_DAYS = 7
MAX_TREES = 2000  # browser_sync.MAX_LIVE["tree"]

# key: (title, botanical name, how it's found)
SPECIES = {
    "NEEM": ("Neem", "Azadirachta indica", "everyone starts with 3"),
    "MANGO": ("Mango", "Mangifera indica", "after the first grown tree"),
    "GULMOHAR": ("Gulmohar", "Delonix regia", "after 2 grown trees"),
    "PEEPAL": ("Peepal", "Ficus religiosa", "after 3 grown trees"),
    "COCONUT": ("Coconut", "Cocos nucifera", "after 5 grown trees"),
    "JASMINE": ("Jasmine", "Jasminum sambac", "after 7 calm days"),
    "BANYAN": ("Banyan", "Ficus benghalensis", "after 10 grown trees"),
    "BAMBOO": ("Bamboo", "Bambusa vulgaris", "7 calm days in a row"),
}
FRUIT = {"PEEPAL", "COCONUT", "BANYAN"}
STAGES = ["SEED", "SPROUT", "SAPLING", "YOUNG", "GROWN"]
STAGE_TITLES = {"SEED": "Seed", "SPROUT": "Sprout", "SAPLING": "Sapling", "YOUNG": "Young tree", "GROWN": "Grown tree"}
SEASONS = {  # India, as in the app: (title, months, note)
    "WINTER": ("Winter", "Dec - Feb", "morning mist"),
    "SUMMER": ("Summer", "Mar - May", "Gulmohar in bloom"),
    "MONSOON": ("Monsoon", "Jun - Sep", "soft rain, deep green"),
    "AUTUMN": ("Autumn", "Oct - Nov", "golden evenings"),
}


def season_of(month):
    return "WINTER" if month in (12, 1, 2) else "SUMMER" if month in (3, 4, 5) else "MONSOON" if month in (6, 7, 8, 9) else "AUTUMN"


def part_of_day(hour):
    return "DAWN" if 5 <= hour <= 6 else "DAY" if 7 <= hour <= 16 else "EVENING" if 17 <= hour <= 18 else "NIGHT"


def needs(stage, grow_days):
    """(calm minutes, ms since planting) a stage needs - Tree.needs in the app."""
    c = max(40, 20 * grow_days)
    t = grow_days * DAY_MS
    return {
        "SEED": (0, 0),
        "SPROUT": (10, 0),
        "SAPLING": (10 + (c - 10) * 25 // 100, t // 4),
        "YOUNG": (10 + (c - 10) * 60 // 100, t // 2),
        "GROWN": (c, t),
    }[stage]


def day_int(d):
    return d.year * 10000 + d.month * 100 + d.day


def int_day(n):
    return date(n // 10000, n // 100 % 100, n % 100)


def duration_text(ms):
    minutes = (ms + 59_999) // 60_000
    if minutes < 60:
        return f"{minutes} min"
    if minutes < 24 * 60:
        return f"{(minutes + 59) // 60} h"
    days = (minutes + 24 * 60 - 1) // (24 * 60)
    return f"{days} day{'' if days == 1 else 's'}"


def minutes_text(m):
    if m < 60:
        return f"{m} min"
    return f"{m // 60} h" if m % 60 == 0 else f"{m // 60} h {m % 60} m"


def grow_days_text(d):
    return {1: "1 day", 7: "1 week", 14: "2 weeks"}.get(d, f"{d} days")


def ms_date(ms):
    return timezone.localtime(datetime.fromtimestamp(ms / 1000, tz=dt_timezone.utc)).strftime("%d %b %Y")


def art_url(species, stage, variant, month, resting=False, crop=False):
    name = f"{species}-{stage}-{variant}-{month}-{1 if resting else 0}{'-c' if crop else ''}"
    return reverse("mobile_forest_art", args=[name])


# ------------------------------------------------------------------ one tree
@dataclass
class Tree:
    id: str
    species: str
    name: str
    planted_ms: int
    grow_days: int
    care: int
    last_care_ms: int
    seeds_given: int
    variant: int
    stages: dict
    updated_ms: int
    by_admin: bool

    @classmethod
    def from_item(cls, item):
        d = item.data or {}
        species = d.get("species") if d.get("species") in SPECIES else "NEEM"
        return cls(
            id=item.key, species=species, name=str(d.get("name") or ""),
            planted_ms=int(d.get("planted_ms") or item.updated_ms or 0),
            grow_days=min(14, max(1, int(d.get("grow_days") or 7))),
            care=int(d.get("care") or 0), last_care_ms=int(d.get("last_care_ms") or 0),
            seeds_given=int(d.get("seeds_given") or 0), variant=int(d.get("variant") or abs(hash(item.key)) % 99_999),
            stages=d.get("stages") or {}, updated_ms=item.updated_ms, by_admin=item.device_id == ADMIN_DEVICE,
        )

    @property
    def title(self):
        return SPECIES[self.species][0]

    @property
    def botanical(self):
        return SPECIES[self.species][1]

    @property
    def calm_needed(self):
        return max(40, 20 * self.grow_days)

    def stage(self, now_ms):
        reached = "SEED"
        for s in STAGES:
            c, t = needs(s, self.grow_days)
            if self.care >= c and now_ms - self.planted_ms >= t:
                reached = s
        return reached

    def stage_name(self, now_ms):
        s = self.stage(now_ms)
        if self.name:
            return self.name
        if s == "GROWN":
            return self.title
        if s == "YOUNG":
            return f"Young {self.title.lower()}"
        return f"{self.title} {STAGE_TITLES[s].lower()}"

    def next_text(self, now_ms, month, resting=False):
        if resting:
            return "Resting - one calm session and it's green"
        s = self.stage(now_ms)
        if s == "GROWN":
            if month in art.SEASONS[self.species]:
                return "Fruiting now" if (self.species in FRUIT or (self.species == "MANGO" and month >= 4)) else "In flower now"
            return f"Grown - gave {self.seeds_given} seed{'' if self.seeds_given == 1 else 's'}"
        nxt = STAGES[STAGES.index(s) + 1]
        c, t = needs(nxt, self.grow_days)
        mins = max(0, c - self.care)
        wait = max(0, self.planted_ms + t - now_ms)
        title = "Young tree" if nxt == "YOUNG" else STAGE_TITLES[nxt]
        if mins and wait:
            return f"{title} in {duration_text(wait)} - {mins} more calm min"
        if mins:
            return f"{title} after {mins} more calm min"
        if wait:
            return f"{title} in {duration_text(wait)}"
        return f"{title} soon"

    def growth(self, now_ms):
        t = min(1.0, max(0.0, (now_ms - self.planted_ms) / (self.grow_days * DAY_MS)))
        c = min(1.0, max(0.0, self.care / self.calm_needed))
        return min(t, c)


# ------------------------------------------------------------------ one account's forest
@dataclass
class Forest:
    account: object
    trees: list = field(default_factory=list)
    minutes_by_day: dict = field(default_factory=dict)  # yyyymmdd -> minutes (all phones)

    @property
    def total_minutes(self):
        return sum(self.minutes_by_day.values())

    @property
    def calm_days(self):
        return sum(1 for m in self.minutes_by_day.values() if m > 0)

    @property
    def last_day(self):
        days = [d for d, m in self.minutes_by_day.items() if m > 0]
        return max(days) if days else None

    def streak(self, today):
        d = today
        if self.minutes_by_day.get(day_int(d), 0) == 0:
            d -= timedelta(days=1)
        n = 0
        while self.minutes_by_day.get(day_int(d), 0) > 0:
            n += 1
            d -= timedelta(days=1)
        return n

    @property
    def longest_streak(self):
        best = run = 0
        prev = None
        for n in sorted(d for d, m in self.minutes_by_day.items() if m > 0):
            day = int_day(n)
            run = run + 1 if prev and prev + timedelta(days=1) == day else 1
            best = max(best, run)
            prev = day
        return best

    def resting(self, today):
        last = self.last_day
        return bool(self.trees) and last is not None and int_day(last) < today - timedelta(days=REST_DAYS)

    def grown_count(self, now_ms):
        return sum(1 for t in self.trees if t.stage(now_ms) == "GROWN")

    def found(self, species, now_ms):
        g = self.grown_count(now_ms)
        return {
            "NEEM": True, "MANGO": g >= 1, "GULMOHAR": g >= 2, "PEEPAL": g >= 3, "COCONUT": g >= 5,
            "JASMINE": self.calm_days >= 7, "BANYAN": g >= 10, "BAMBOO": self.longest_streak >= 7,
        }[species]

    def seeds(self, species, now_ms):
        gift = 0 if not self.found(species, now_ms) else 3 if species == "NEEM" else 1
        kind = [t for t in self.trees if t.species == species]
        return max(0, gift + sum(t.seeds_given for t in kind) - len(kind))

    @property
    def lotus(self):
        return self.total_minutes >= 100 * 60

    def visitors(self, now_ms, month):
        grown = [t for t in self.trees if t.stage(now_ms) == "GROWN"]
        in_season = sum(1 for t in grown if month in art.SEASONS[t.species])
        big = sum(1 for t in grown if t.species in ("BANYAN", "PEEPAL"))
        return {
            "butterflies": min(6, 1 if in_season == 0 and grown else in_season),
            "fireflies": min(20, len(grown)),
            "birds": 0 if not grown else min(5, 1 + big * 2),
        }


def load_many(accounts):
    """{account id: Forest} for these accounts, in two queries."""
    forests = {a.pk: Forest(a) for a in accounts}
    items = SyncItem.objects.filter(account_id__in=list(forests), kind__in=["tree", "calmlog"], deleted=False)
    for item in items:
        f = forests[item.account_id]
        if item.kind == "tree":
            f.trees.append(Tree.from_item(item))
        else:
            try:
                day = int(item.key.split("|")[0])
            except ValueError:
                continue
            f.minutes_by_day[day] = f.minutes_by_day.get(day, 0) + int((item.data or {}).get("min") or 0)
    for f in forests.values():
        f.trees.sort(key=lambda t: t.planted_ms)
    return forests


def load(account):
    return load_many([account])[account.pk]


# ------------------------------------------------------------------ giving and removing trees
def new_tree_data(species, stage, grow_days, name, now_ms):
    """The app's Tree.syncJson for a tree that is at `stage` right now. seeds_given is 1 so a gift
    never costs the user one of their own seeds (and a grown one doesn't add one)."""
    care, wait = needs(stage, grow_days)
    planted = now_ms - wait - (HOUR_MS if wait else 60_000)
    return {
        "species": species, "name": name, "planted_ms": planted, "grow_days": grow_days, "care": care,
        "last_care_ms": now_ms if care else 0, "seeds_given": 1, "season_year": 0,
        "variant": random.randint(1, 99_999),
        "stages": {s: planted + needs(s, grow_days)[1] for s in STAGES[: STAGES.index(stage) + 1]},
    }


def add_trees(account, species, stage, grow_days, count, name=""):
    now_ms = int(time.time() * 1000)
    keys = []
    for i in range(count):
        key = str(uuid.uuid4())
        tree_name = f"{name} {i + 1}" if name and count > 1 else name
        SyncItem.objects.create(
            account=account, kind="tree", key=key, data=new_tree_data(species, stage, grow_days, tree_name, now_ms),
            updated_ms=now_ms, deleted=False, device_id=ADMIN_DEVICE,
        )
        keys.append(key)
    ping(account)
    return keys


def remove_tree(account, key):
    item = SyncItem.objects.filter(account=account, kind="tree", key=key, deleted=False).first()
    if item is None:
        return None
    title = Tree.from_item(item).stage_name(int(time.time() * 1000))
    item.data = {}
    item.deleted = True
    item.updated_ms = max(int(time.time() * 1000), item.updated_ms + 1)  # newer than any phone's copy
    item.device_id = ADMIN_DEVICE
    item.save()
    ping(account)
    return title


def ping(account):
    """Tell the user's phones to fetch their forest now (silent push; best-effort)."""
    from .browser_sync import _ping_other_phones
    _ping_other_phones(account, "", "forest")


# ------------------------------------------------------------------ the scene (as the phone draws it)
W = 390          # one screen of forest
H = 600          # the scene behind the phone's top part; the card covers below ~560
ROW_Y = [.58, .70, .82, .935]
ROW_SCALE = [.6, .74, .87, 1.0]
ROW_CAP = [4, 4, 4, 5]
SPECIES_SCALE = {"BANYAN": 1.18, "GULMOHAR": 1.12, "MANGO": 1.05, "NEEM": .98, "PEEPAL": .88, "COCONUT": .84, "BAMBOO": .72, "JASMINE": .82}
STAGE_SCALE = {"SEED": .42, "SPROUT": .5, "SAPLING": .66, "YOUNG": .82, "GROWN": 1.0}
GB = 560         # where the front row stands (the card starts just below)


def layout(trees, now_ms, pond):
    """Grown trees at the back, growing ones in front; more trees -> a wider forest (the app's layout)."""
    grown = [t for t in trees if t.stage(now_ms) == "GROWN"]
    growing = [t for t in trees if t.stage(now_ms) != "GROWN"]
    pages = 1
    while True:
        rows = [[] for _ in range(4)]
        cap = [c * pages for c in ROW_CAP]
        if pond:
            cap[3] -= 2

        def put(t, order):
            for i in order:
                if len(rows[i]) < cap[i]:
                    rows[i].append(t)
                    return True
            return False

        if (all(put(t, (3, 2, 1)) for t in growing) and all(put(t, (1, 0, 2, 3)) for t in grown)) or pages >= 15:
            break
        pages += 1
    scene_w = W * pages
    placed = []
    for ri, row in enumerate(rows):
        usable = scene_w - W * .42 if ri == 3 and pond else scene_w
        n = len(row)
        for i, t in enumerate(row):
            rnd = random.Random(t.variant * 31 + 7)
            slot = usable / n
            st = t.stage(now_ms)
            size = (W * .42 * SPECIES_SCALE[t.species] if st == "GROWN" else W * .30 * STAGE_SCALE[st]) * ROW_SCALE[ri]
            size = min(size, W * .5)
            x = (i + .5) * slot + (rnd.random() - .5) * slot * .28 + (slot * .06 if ri % 2 else 0)
            base = GB * ROW_Y[ri] + (rnd.random() - .5) * GB * .022
            placed.append({"tree": t, "stage": st, "x": min(max(x, size * .3), scene_w - size * .3), "base": base, "w": size, "row": ri})
    placed.sort(key=lambda p: p["base"])
    return placed, scene_w


LOOKS = {
    "NIGHT": (("#0B1626", "#16304A", "#1E4052"), "#203C48", "#1B3442", "#9FB8C9", "#20384A"),
    "RAIN": (("#8FA6B2", "#B9C7CC", "#DCE2DD"), "#9CB2AE", "#7E9E8C", "#E3E8E4", "#D5E0DE"),
    "DAWN": (("#F2B8A2", "#F7D9C4", "#F6EDD6"), "#B8B9C2", "#93A79A", "#FBE9DC", "#F1E2DA"),
    "EVENING": (("#E79A74", "#F4C89C", "#F7E5C6"), "#B2A6A2", "#899A82", "#F7E2C4", "#F2DCC4"),
    "DAY": (("#9FD3E6", "#D7EEE9", "#F6EDD6"), "#A9C7BC", "#86AE95", "#F6EDD6", "#DCEFEA"),
}
MEADOWS = {
    "SUMMER": ("#BCCB74", "#9FBB5E", "#6F9447"), "MONSOON": ("#8CC867", "#6CAE52", "#4A8A3E"),
    "AUTUMN": ("#B9C26A", "#9BAE58", "#6F9046"), "WINTER": ("#A4CB86", "#86B46F", "#5F9455"),
}


def scene_svg(forest, local_now, sky="now"):
    """The forest, drawn like the app's home: sky of the hour and season, hills in mist, a meadow
    with a path, every tree (swaying), and its visitors. `sky`: "now", "day" or "night"."""
    F = art.F
    now_ms = int(local_now.timestamp() * 1000)
    month = local_now.month
    hour = {"day": 10, "night": 21}.get(sky, local_now.hour)
    part = part_of_day(hour)
    season = season_of(month)
    today = local_now.date()
    rainy = season == "MONSOON" and (day_int(today) * 7 + 3) % 3 != 0
    resting = forest.resting(today)
    night = part == "NIGHT"
    look = LOOKS["NIGHT" if night else "RAIN" if rainy else part]
    sky_c, hill, treeline, mist, haze = look
    mead = ("#1D3B2C", "#16301F", "#0E2218") if night else MEADOWS[season]
    placed, sw = layout(forest.trees, now_ms, forest.lotus)
    vis = forest.visitors(now_ms, month)
    rnd = random.Random(forest.account.pk * 13 + 5)
    pages = int(sw // W)

    o = [f'<svg class="forest-scene" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {F(sw)} {H}" width="{F(sw)}" height="{H}" role="img" aria-label="Forest">']
    o.append(f'<defs>{art.SCENE_DEFS}'
             f'<linearGradient id="skyg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{sky_c[0]}"/><stop offset=".45" stop-color="{sky_c[1]}"/><stop offset="1" stop-color="{sky_c[2]}"/></linearGradient>'
             f'<linearGradient id="meadg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{mead[0]}"/><stop offset=".5" stop-color="{mead[1]}"/><stop offset="1" stop-color="{mead[2]}"/></linearGradient>'
             f'<linearGradient id="mistg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{mist}" stop-opacity="0"/><stop offset=".5" stop-color="{mist}" stop-opacity="{.22 if night else .75}"/><stop offset="1" stop-color="{mist}" stop-opacity="0"/></linearGradient>'
             f'<linearGradient id="hazeg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{haze}" stop-opacity="0"/><stop offset=".5" stop-color="{haze}" stop-opacity="{.15 if night else .3}"/><stop offset="1" stop-color="{haze}" stop-opacity="0"/></linearGradient>'
             '<linearGradient id="trailg" x1="0" y1="1" x2="0" y2="0"><stop offset="0" stop-color="#E9D7AE"/><stop offset="1" stop-color="#F2E6C6"/></linearGradient></defs>')
    o.append(f'<rect width="{F(sw)}" height="340" fill="url(#skyg)"/><rect y="339" width="{F(sw)}" height="{H - 339}" fill="{sky_c[2]}"/>')

    # Sun or moon, stars, clouds, birds.
    if night:
        for _ in range(46 * pages):
            o.append(f'<circle cx="{F(rnd.uniform(0, sw))}" cy="{F(rnd.uniform(0, 250))}" r="{F(rnd.uniform(.5, 1.5))}" fill="#F3F6FF">'
                     f'<animate attributeName="opacity" values="1;.3;1" dur="{F(rnd.uniform(2, 6))}s" repeatCount="indefinite"/></circle>')
        o.append('<circle cx="300" cy="104" r="80" fill="url(#fMoon)"/><circle cx="300" cy="104" r="22" fill="#F4ECD2"/>'
                 '<circle cx="292" cy="98" r="4" fill="#E2D7B6" opacity=".7"/><circle cx="306" cy="112" r="3" fill="#E2D7B6" opacity=".6"/>')
    else:
        sun = {"DAWN": (62, 210), "EVENING": (328, 200)}.get(part, (78, 118))
        o.append(f'<circle cx="{sun[0]}" cy="{sun[1]}" r="120" fill="url(#fSun)" opacity="{.45 if rainy else 1}"/>')
        if not rainy:
            o.append(f'<circle cx="{sun[0]}" cy="{sun[1]}" r="24" fill="#FFF7DC"/>')
        clouds = [(230, 92, 1.1), (320, 150, .7), (150, 60, .6)] + ([(60, 120, 1.1), (380, 70, .8)] if rainy else [])
        for p in range(pages):
            for cx, cy, s in clouds:
                o.append(art.cloud(cx + p * W, cy, s * (1.2 if rainy else 1), .85, "#E6EBED" if rainy else "#fff"))
        for i in range(max(1, vis["birds"])):
            o.append(art.bird(rnd.uniform(180, 300), rnd.uniform(100, 150), rnd.uniform(.7, 1.1), rnd.randint(13, 20)))

    # Far hills, tree line, mist.
    hp = "M-10 262"
    x = -10
    y = 262
    while x < sw + 20:
        nx, ny = x + W / 3.2, rnd.uniform(220, 256)
        hp += f"Q{F(x + (nx - x) / 2)} {F(y + (ny - y) * .2 - 14)} {F(nx)} {F(ny)}"
        x, y = nx, ny
    o.append(f'<path d="{hp}L{F(sw + 20)} 320L-10 320Z" fill="{hill}"/>')
    o.append(f'<g fill="{treeline}">' + "".join(f'<circle cx="{F(tx)}" cy="{F(268 + rnd.uniform(-4, 4))}" r="{F(rnd.uniform(6, 11))}"/>' for tx in range(0, int(sw) + 10, 9)) + f'<rect y="268" width="{F(sw)}" height="32"/></g>')
    o.append(f'<rect y="246" width="{F(sw)}" height="60" fill="url(#mistg)"/>')

    # Meadow and path.
    mp = "M0 296" + "".join(f"C{F(p * W + 70)} 278 {F(p * W + 140)} 284 {F(p * W + 206)} 292S{F(p * W + 330)} 282 {F(p * W + 390)} 290" for p in range(pages))
    o.append(f'<path d="{mp}V{H}H0Z" fill="url(#meadg)"/>')
    o.append(f'<path d="M168 {H}C176 470 236 430 214 380S196 320 220 296L230 296C214 322 236 350 236 384S206 470 232 {H}Z" fill="url(#trailg)" opacity="{.35 if night else 1}"/>')

    # Rows of trees (back to front), grass between.
    bands = {0: (70, (0, 300, sw, 360), (3, 6)), 1: (90, (0, 380, sw, 460), (4, 8)), 3: (70, (0, 470, sw, H), (6, 12))}
    for row in range(4):
        if row == 3 and forest.lotus:
            pw = W * .4
            o.append(f'<image href="{reverse("mobile_forest_pond")}" x="{F(sw - W * .2 - pw / 2)}" y="{F(GB * .975 - pw * 1.2 * 226 / 240)}" width="{F(pw)}" height="{F(pw * 1.2)}"/>')
        for p in placed:
            if p["row"] != row:
                continue
            t, w = p["tree"], p["w"]
            h = w * 1.2
            href = art_url(t.species, p["stage"], t.variant, month, resting)
            dur = 5.5 + t.variant % 3
            o.append(f'<image class="tree{" small" if p["stage"] != "GROWN" else ""}" data-tree="{t.id}" href="{href}" x="{F(p["x"] - w / 2)}" y="{F(p["base"] - h * 226 / 240)}" '
                     f'width="{F(w)}" height="{F(h)}" style="animation-duration:{F(dur)}s;animation-delay:-{F(t.variant % 7)}s">'
                     f'<title>{_esc(t.stage_name(now_ms))} - {_esc(t.next_text(now_ms, month, resting))}</title></image>')
        if row == 0:
            o.append(f'<rect y="170" width="{F(sw)}" height="170" fill="url(#hazeg)"/><rect y="272" width="{F(sw)}" height="60" fill="url(#mistg)" opacity=".6"/>')
        if row in bands:
            n, box, hh = bands[row]
            o.append(art.tufts(int(n * pages), box, row + 3, hh))
        if row == 3 and not night:
            o.append(art.wildflowers(int(60 * pages), (0, 330, sw, 590), 8))

    # Visitors and weather.
    if not night and not rainy and vis["butterflies"]:
        grown = [p for p in placed if p["stage"] == "GROWN"]
        near = [p for p in grown if month in art.SEASONS[p["tree"].species]] or grown
        for i in range(vis["butterflies"]):
            p = near[i % len(near)] if near else None
            cx, cy = (p["x"], p["base"] - p["w"] * .55) if p else (W / 2, 420)
            o.append(art.butterfly(i, cx, cy, rnd.uniform(18, 26), rnd.uniform(8, 12), 1 + rnd.random(), rnd.uniform(0, 3)))
    if night:
        o.append(f'<rect width="{F(sw)}" height="{H}" fill="#0A1A2C" opacity=".42"/>')
        o.append(f'<rect y="300" width="{F(sw)}" height="80" fill="url(#mistg)"/><rect y="470" width="{F(sw)}" height="70" fill="url(#mistg)"/>')
        o.append(art.fireflies(vis["fireflies"], (10, 330, sw - 10, 560), forest.account.pk))
    if rainy and not night:
        o.append(art.rain(sw, H, 3))
    if season == "AUTUMN" or resting:
        for i in range(3 * pages):
            o.append(art.falling_leaf(rnd.uniform(20, sw - 20), rnd.uniform(160, 320), rnd.randint(8, 12), ["#D9A441", "#C58A2C", "#C99B45"][i % 3]))
    tint = {"EVENING": ("#F6B25C", .1), "DAWN": ("#F7B9A0", .06)}.get(part) or (("#F6B25C", .06) if season == "AUTUMN" and not night else None)
    if tint:
        o.append(f'<rect width="{F(sw)}" height="{H}" fill="{tint[0]}" opacity="{tint[1]}"/>')
    if resting and not night:
        o.append(f'<rect width="{F(sw)}" height="{H}" fill="#D9A441" opacity=".05"/>')
    o.append("</svg>")
    return "".join(o), {"part": part, "season": season, "rainy": rainy, "resting": resting, "night": night, "visitors": vis, "width": sw}


def week_svg(forest, today, goal=20):
    """The last 7 days as little plants: taller with more calm minutes, a flower on goal days."""
    F = art.F
    days = [today - timedelta(days=6 - i) for i in range(7)]
    mins = [forest.minutes_by_day.get(day_int(d), 0) for d in days]
    per = min(3.0, 150 / max(goal, max(mins), 1))
    rnd = random.Random(3)
    s = ['<svg viewBox="0 0 358 210" class="week-svg" role="img" aria-label="Calm minutes, last 7 days">',
         '<path d="M0 182h358" stroke="#B9AE8E" stroke-width="1.5"/>']
    gy = 182 - goal * per
    s.append(f'<path d="M0 {F(gy)}H358" stroke="#9DBF86" stroke-width="1.6" stroke-dasharray="2 5"/>'
             f'<text x="354" y="{F(gy - 6)}" text-anchor="end" font-size="10" fill="currentColor" opacity=".6">{goal} min goal</text>')
    for i, (d, m) in enumerate(zip(days, mins)):
        x = 26 + i * 51
        is_today = i == 6
        col = "#2F5D3A" if is_today else "#5E9A54"
        s.append(f'<g><title>{d.strftime("%a %d %b")}: {m} calm min</title>')
        if m > 0:
            top = 182 - max(m * per, 16)
            s.append(f'<path d="M{x} 182C{x - 2} {F((182 + top) / 2)} {x + 2} {F((182 + top) / 2)} {x} {F(top)}" stroke="{col}" stroke-width="3" fill="none" stroke-linecap="round"/>')
            y, k = 172, 0
            while y > top + 6:
                s.append(art.leaf(x, y, 13, 4.5, (-30 if k % 2 == 0 else -150) + rnd.uniform(-6, 6), "#4E8F45" if is_today else "#7DB55A"))
                y -= 12
                k += 1
            if m >= goal:
                s.append(f'<circle cx="{x}" cy="{F(top - 2)}" r="5.5" fill="{"#E8B04B" if is_today else "#F2A7B8"}"/><circle cx="{x}" cy="{F(top - 2)}" r="2" fill="#FFF3C4"/>')
        else:
            s.append(f'<ellipse cx="{x}" cy="180" rx="5" ry="2.4" fill="#8C6239"/>')
        s.append("</g>")
        s.append(f'<text x="{x}" y="200" text-anchor="middle" font-size="11.5" fill="currentColor" opacity="{1 if is_today else .6}" font-weight="{500 if is_today else 400}">{d.strftime("%a")[0]}</text>')
    s.append("</svg>")
    return "".join(s), sum(mins), sum(1 for m in mins if m > 0), max(mins)


def _esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
