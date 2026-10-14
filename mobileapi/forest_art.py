"""Forest's drawings for the admin pages: the same trees as the app (forest/ForestArt.kt), as SVG.

Every tree is 200 x 240 with the trunk's foot at (100, 226). A leaf mass is many small wobbly blobs,
lighter toward the sun (upper left), over a darker back layer. A tree's `variant` seeds the
randomness, so each tree looks like itself everywhere (and like it does on the phone, near enough).
`tree_svg` is a whole SVG file (served at /mobile/forests/art/...); the scene pieces are fragments
for the forest page.
"""
import math
import random
from functools import lru_cache

LIGHT = (-0.55, -0.83)  # light comes from the upper left
AUTUMN = ("#7A5A2A", "#977236", "#B48E4A", "#D2B171")  # a resting tree's gold leaves


def F(v):
    s = f"{v:.1f}"
    return s[:-2] if s.endswith(".0") else s


# ------------------------------------------------------------------ shapes
def blob(cx, cy, r, rnd, n=8, wob=0.17, squash=0.9):
    off = rnd.uniform(0, 2 * math.pi)
    pts = []
    for i in range(n):
        a = off + 2 * math.pi * i / n
        rr = r * (1 + rnd.uniform(-wob, wob))
        pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a) * squash))

    def mid(p, q):
        return ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)

    m0 = mid(pts[-1], pts[0])
    d = f"M{F(m0[0])} {F(m0[1])}"
    for i in range(n):
        p, q = pts[i], pts[(i + 1) % n]
        m = mid(p, q)
        d += f"Q{F(p[0])} {F(p[1])} {F(m[0])} {F(m[1])}"
    return d + "Z"


def canopy(inside, box, n, rmin, rmax, pal, seed, back=0.7, jitter=0.28, cuts=(-0.35, 0.1, 0.55)):
    """Leaf mass: a darker back layer, then blobs from shadow to highlight (light ones on top)."""
    rnd = random.Random(seed)
    x0, y0, x1, y1 = box
    cx, cy, w, h = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2, (y1 - y0) / 2
    items = []
    guard = 0
    while len(items) < n and guard < n * 200:
        guard += 1
        x, y = rnd.uniform(x0, x1), rnd.uniform(y0, y1)
        if not inside(x, y):
            continue
        r = rnd.uniform(rmin, rmax)
        nx, ny = (x - cx) / w, (y - cy) / h
        lum = (nx * LIGHT[0] + ny * LIGHT[1]) + rnd.uniform(-jitter, jitter)
        items.append((lum, x, y, r))
    items.sort()
    out = []
    backs = [it for it in items if rnd.random() < back]
    out.append(f'<path fill="{pal[0]}" d="' + "".join(blob(x, y + r * .25, r * 1.12, rnd) for _, x, y, r in backs) + '"/>')
    groups = {0: [], 1: [], 2: [], 3: []}
    for lum, x, y, r in items:
        k = 0 if lum < cuts[0] else 1 if lum < cuts[1] else 2 if lum < cuts[2] else 3
        groups[k].append(blob(x, y, r, rnd))
    for k in range(4):
        if groups[k]:
            out.append(f'<path fill="{pal[k]}" d="' + "".join(groups[k]) + '"/>')
    return "".join(out)


def ell(cx, cy, rx, ry):
    return lambda x, y: ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 <= 1


def union(*fs):
    return lambda x, y: any(f(x, y) for f in fs)


def leaf(x, y, length, width, ang, fill, rib=None, rib_w=0.8):
    a = math.radians(ang)
    ca, sa = math.cos(a), math.sin(a)

    def pt(u, v):
        return (x + u * ca - v * sa, y + u * sa + v * ca)

    tip, c1, c2 = pt(length, 0), pt(length * 0.42, width), pt(length * 0.42, -width)
    d = f"M{F(x)} {F(y)}Q{F(c1[0])} {F(c1[1])} {F(tip[0])} {F(tip[1])}Q{F(c2[0])} {F(c2[1])} {F(x)} {F(y)}Z"
    s = f'<path d="{d}" fill="{fill}"/>'
    if rib:
        e = pt(length * 0.86, 0)
        s += f'<path d="M{F(x)} {F(y)}L{F(e[0])} {F(e[1])}" stroke="{rib}" stroke-width="{rib_w}" stroke-linecap="round"/>'
    return s


def heart(x, y, s, ang, fill):
    """A peepal leaf: round base, long drip tip."""
    a = math.radians(ang)
    ca, sa = math.cos(a), math.sin(a)

    def pt(u, v):
        return (x + u * ca - v * sa, y + u * sa + v * ca)

    p = [pt(0, 0), pt(-s * .3, s * .7), pt(s * .55, s * .9), pt(s * 1.0, s * .35), pt(s * 1.7, 0), pt(s * 1.0, -s * .35), pt(s * .55, -s * .9), pt(-s * .3, -s * .7)]
    q1, q2 = pt(s * 1.35, s * .1), pt(s * 1.35, -s * .1)
    d = f"M{F(p[0][0])} {F(p[0][1])}C{F(p[1][0])} {F(p[1][1])} {F(p[2][0])} {F(p[2][1])} {F(p[3][0])} {F(p[3][1])}"
    d += f"Q{F(q1[0])} {F(q1[1])} {F(p[4][0])} {F(p[4][1])}Q{F(q2[0])} {F(q2[1])} {F(p[5][0])} {F(p[5][1])}"
    d += f"C{F(p[6][0])} {F(p[6][1])} {F(p[7][0])} {F(p[7][1])} {F(p[0][0])} {F(p[0][1])}Z"
    return f'<path d="{d}" fill="{fill}"/>'


def trunk(x_base, w_base, x_top, w_top, y_top, y_base=226, grad="url(#gBark)", bend=0):
    d = (f"M{F(x_base - w_base / 2)} {y_base}"
         f"Q{F((x_base + x_top) / 2 - w_base / 2 + bend)} {F((y_base + y_top) / 2)} {F(x_top - w_top / 2)} {F(y_top)}"
         f"L{F(x_top + w_top / 2)} {F(y_top)}"
         f"Q{F((x_base + x_top) / 2 + w_base / 2 + bend)} {F((y_base + y_top) / 2)} {F(x_base + w_base / 2)} {y_base}Z")
    return f'<path d="{d}" fill="{grad}"/>'


def branch(x0, y0, x1, y1, w0, w1=1.2, grad="url(#gBark)", bow=0):
    ang = math.atan2(y1 - y0, x1 - x0)
    nx, ny = -math.sin(ang), math.cos(ang)
    mx, my = (x0 + x1) / 2 + nx * bow, (y0 + y1) / 2 + ny * bow
    a = (x0 + nx * w0 / 2, y0 + ny * w0 / 2)
    b = (x0 - nx * w0 / 2, y0 - ny * w0 / 2)
    c = (x1 - nx * w1 / 2, y1 - ny * w1 / 2)
    e = (x1 + nx * w1 / 2, y1 + ny * w1 / 2)
    return (f'<path d="M{F(a[0])} {F(a[1])}Q{F(mx)} {F(my)} {F(e[0])} {F(e[1])}L{F(c[0])} {F(c[1])}'
            f'Q{F(mx)} {F(my)} {F(b[0])} {F(b[1])}Z" fill="{grad}"/>')


def ground(rx=60, tufts=7, seed=1, y=226):
    rnd = random.Random(seed)
    s = f'<ellipse cx="100" cy="{y + 3}" rx="{rx}" ry="{F(rx * .13)}" fill="#1B2A12" opacity=".22" filter="url(#soft)"/>'
    for _ in range(tufts):
        x = rnd.uniform(100 - rx * .8, 100 + rx * .8)
        h = rnd.uniform(5, 10)
        c = rnd.choice(["#4E8A3A", "#5E9A44", "#6FA84E"])
        s += (f'<path d="M{F(x)} {y + 2}q-2 -{F(h * .6)} -4 -{F(h)}M{F(x)} {y + 2}q0 -{F(h * .7)} 1 -{F(h * 1.1)}'
              f'M{F(x)} {y + 2}q2 -{F(h * .5)} 5 -{F(h * .8)}" stroke="{c}" stroke-width="1.3" fill="none" stroke-linecap="round"/>')
    return s


def fallen(seed):
    rnd = random.Random(seed)
    return "".join(leaf(rnd.uniform(40, 160), 228 + rnd.uniform(-2, 2), 6, 2.4, rnd.uniform(0, 360), rnd.choice(["#C99B45", "#B07D33", "#D9B464"])) for _ in range(9))


def dots(shape, box, n, color, r, seed):
    rnd = random.Random(seed)
    x0, y0, x1, y1 = box
    out = ""
    for _ in range(n):
        x, y = rnd.uniform(x0, x1), rnd.uniform(y0, y1)
        if shape(x, y):
            out += f'<circle cx="{F(x)}" cy="{F(y)}" r="{r}" fill="{color}"/>'
    return out


# ------------------------------------------------------------------ the kinds of trees
def neem(seed, resting=False, flowers=False):
    s = ground(70, 8, seed)
    s += trunk(100, 15, 101, 8, 140)
    s += branch(100, 160, 62, 112, 7, 2) + branch(101, 150, 140, 104, 7, 2) + branch(100, 142, 98, 74, 6, 2, bow=4) + branch(99, 170, 76, 140, 4, 1.5)
    shape = union(ell(64, 106, 40, 28), ell(137, 100, 40, 30), ell(100, 70, 46, 34), ell(84, 132, 26, 15), ell(120, 130, 28, 15))
    s += canopy(shape, (20, 30, 180, 150), 210, 5.5, 10, AUTUMN if resting else ("#284F27", "#376B32", "#4F8A40", "#7DB35C"), seed, back=0.55)
    if flowers and not resting:
        s += dots(shape, (20, 30, 180, 120), 70, "#F8F4E6", 1.2, seed + 3)
    return s + (fallen(seed) if resting else "")


def mango(seed, resting=False, fruit=False, flowers=False):
    pal = AUTUMN if resting else ("#173F25", "#235A33", "#327443", "#55965C")
    s = ground(74, 8, seed)
    s += trunk(100, 18, 100, 10, 160)
    s += branch(100, 168, 66, 132, 7, 2) + branch(100, 166, 136, 130, 7, 2)
    s += f'<ellipse cx="100" cy="112" rx="74" ry="54" fill="{pal[0]}"/>'
    s += canopy(lambda x, y: ell(100, 108, 80, 62)(x, y) and y < 166, (20, 46, 180, 166), 230, 8, 13.5, pal, seed, back=0.4)
    rnd = random.Random(seed + 5)
    for _ in range(14):  # glossy leaf glints
        x, y = rnd.uniform(48, 120), rnd.uniform(60, 120)
        s += f'<ellipse cx="{F(x)}" cy="{F(y)}" rx="3.2" ry="1.2" fill="#FFFFFF" opacity=".16" transform="rotate(-30 {F(x)} {F(y)})"/>'
    if fruit and not resting:
        for x, y in [(52, 150), (76, 158), (100, 154), (126, 160), (148, 146), (88, 140), (138, 132)]:
            s += f'<path d="M{x} {y - 8}v4" stroke="#5A3B1E" stroke-width="1"/>'
            s += f'<ellipse cx="{x}" cy="{y}" rx="5.2" ry="7" fill="url(#gMango)" transform="rotate(-12 {x} {y})"/>'
            s += f'<ellipse cx="{x - 1.6}" cy="{y - 2.5}" rx="1.4" ry="2.2" fill="#FFF7D6" opacity=".55"/>'
    if flowers and not resting:
        for x, y in [(48, 82), (70, 62), (96, 54), (124, 58), (150, 76), (82, 92), (116, 88)]:
            for i in range(6):
                s += f'<circle cx="{F(x + rnd.uniform(-3, 3))}" cy="{F(y - i * 2.4)}" r="{F(2.4 - i * .25)}" fill="#F3E3B0"/>'
    return s + (fallen(seed) if resting else "")


def gulmohar(seed, resting=False, bloom=True):
    pal = AUTUMN if resting else ("#2F5C24", "#427A2F", "#5F9A43", "#8DC067")
    s = ground(86, 9, seed)
    s += trunk(100, 16, 100, 9, 150, bend=-2)
    s += branch(100, 156, 42, 108, 8, 2, bow=-6) + branch(100, 152, 160, 104, 8, 2, bow=6) + branch(100, 150, 96, 96, 6, 2)

    def shape(x, y):
        return ell(100, 100, 94, 36)(x, y) and y < 124

    s += canopy(shape, (6, 64, 194, 124), 230, 5.5, 9, pal, seed, back=0.55)
    if bloom and not resting:
        rnd = random.Random(seed + 9)
        fl = []
        for _ in range(150):
            x, y = rnd.uniform(10, 190), rnd.uniform(66, 116)
            if not shape(x, y) or (y > 104 and rnd.random() < .6):
                continue
            nx, ny = (x - 100) / 94, (y - 90) / 36
            lum = (nx * LIGHT[0] + ny * LIGHT[1]) + rnd.uniform(-.3, .3)
            fl.append((lum, x, y, rnd.uniform(2.4, 4.6)))
        fl.sort()
        cols = ["#B5261A", "#D63A23", "#EF5B2E", "#F8904A"]
        for lum, x, y, r in fl:
            c = cols[0 if lum < -.3 else 1 if lum < .15 else 2 if lum < .55 else 3]
            s += f'<path d="{blob(x, y, r, rnd, n=6, wob=.3)}" fill="{c}"/>'
    return s + (fallen(seed) if resting else "")


def banyan(seed, resting=False):
    pal = AUTUMN if resting else ("#1B4A2C", "#276338", "#38804A", "#5FA266")
    rnd = random.Random(seed)
    s = ground(96, 10, seed)
    s += trunk(100, 26, 100, 16, 120)
    s += trunk(46, 8, 50, 6, 128) + trunk(156, 8, 152, 6, 128)
    s += branch(100, 130, 36, 112, 10, 3) + branch(100, 128, 166, 110, 10, 3)
    for _ in range(46):  # aerial roots
        x = rnd.uniform(18, 182)
        y0 = rnd.uniform(118, 134)
        y1 = rnd.uniform(150, 226) if rnd.random() < .7 else 226
        s += (f'<path d="M{F(x)} {F(y0)}q{F(rnd.uniform(-2, 2))} {F((y1 - y0) / 2)} {F(rnd.uniform(-1.5, 1.5))} {F(y1 - y0)}" '
              f'stroke="#7A5B40" stroke-width="{F(rnd.uniform(.6, 1.6))}" opacity="{F(rnd.uniform(.55, .9))}" fill="none"/>')
    s += f'<ellipse cx="100" cy="98" rx="90" ry="38" fill="{pal[0]}"/>'
    s += canopy(lambda x, y: ell(100, 92, 98, 50)(x, y) and y < 132, (2, 42, 198, 132), 260, 7.5, 12.5, pal, seed, back=0.45)
    return s + (fallen(seed) if resting else "")


def peepal(seed, resting=False):
    pal = AUTUMN if resting else ("#355F27", "#4B7F33", "#69A146", "#98C766")
    rnd = random.Random(seed)
    s = ground(66, 8, seed)
    s += trunk(100, 16, 100, 9, 148, bend=2)
    s += branch(100, 156, 70, 110, 6, 2) + branch(100, 150, 134, 104, 6, 2)
    s += canopy(ell(100, 94, 66, 64), (34, 30, 166, 158), 170, 7.5, 12, pal, seed, back=0.5)
    for _ in range(38):
        a = rnd.uniform(-170, -10) if rnd.random() < .7 else rnd.uniform(-200, 20)
        r = 64 + rnd.uniform(-4, 4)
        x, y = 100 + r * math.cos(math.radians(a)), 94 + r * math.sin(math.radians(a)) * .97
        c = "#D2B171" if resting else rnd.choice(["#7DB44F", "#98C766", "#69A146"])
        s += heart(x, y, rnd.uniform(5, 7), a + rnd.uniform(-25, 25), c)
    return s + (fallen(seed) if resting else "")


def coconut(seed):
    rnd = random.Random(seed)
    s = ground(40, 6, seed)
    pts = []
    for i in range(21):
        t = i / 20
        x = (1 - t) ** 2 * 100 + 2 * (1 - t) * t * 98 + t * t * 124
        y = (1 - t) ** 2 * 226 + 2 * (1 - t) * t * 140 + t * t * 66
        pts.append((x, y, 9 - 3.2 * t))
    left = " ".join(f"{F(x - w / 2)},{F(y)}" for x, y, w in pts)
    right = " ".join(f"{F(x + w / 2)},{F(y)}" for x, y, w in reversed(pts))
    s += f'<polygon points="{left} {right}" fill="url(#gPalm)"/>'
    for x, y, w in pts[1:-1]:
        s += f'<path d="M{F(x - w / 2)} {F(y)}q{F(w / 2)} 2 {F(w)} 0" stroke="#5B442C" stroke-width=".7" fill="none" opacity=".55"/>'
    cx, cy = 124, 66
    for k in range(11):
        ang = -180 + k * 18 + rnd.uniform(-6, 6)
        length = rnd.uniform(66, 84)
        droop = 26 + 18 * abs(math.cos(math.radians(ang)))
        ex, ey = cx + length * math.cos(math.radians(ang)), cy + length * math.sin(math.radians(ang)) * .55 + droop
        mx, my = cx + length * .5 * math.cos(math.radians(ang)), cy + length * .5 * math.sin(math.radians(ang)) * .55 - 8
        col = rnd.choice(["#2E7A33", "#3A8C3B", "#4E9E46"])
        s += f'<path d="M{cx} {cy}Q{F(mx)} {F(my)} {F(ex)} {F(ey)}" stroke="#5E8F3A" stroke-width="1.6" fill="none"/>'
        for i in range(2, 17):
            t = i / 17
            px = (1 - t) ** 2 * cx + 2 * (1 - t) * t * mx + t * t * ex
            py = (1 - t) ** 2 * cy + 2 * (1 - t) * t * my + t * t * ey
            tx = 2 * (1 - t) * (mx - cx) + 2 * t * (ex - mx)
            ty = 2 * (1 - t) * (my - cy) + 2 * t * (ey - my)
            base = math.degrees(math.atan2(ty, tx))
            lf = 17 * (1 - .55 * t)
            for side in (1, -1):
                s += leaf(px, py, lf, 1.7, base + side * 62 + rnd.uniform(-8, 8), col)
    for x, y in [(119, 72), (127, 73), (123, 78), (130, 69)]:
        s += f'<circle cx="{x}" cy="{y}" r="4.2" fill="url(#gNut)"/>'
    return s


def bamboo(seed):
    rnd = random.Random(seed)
    s = ground(44, 7, seed)
    leaves = ""
    for x, top, lean in [(72, 60, -3), (84, 30, -1), (96, 14, 0), (108, 40, 1), (120, 22, 2), (132, 70, 4)]:
        s += f'<path d="M{x - 3.4} 226L{F(x + lean - 2.6)} {top}h5.2L{x + 3.4} 226z" fill="url(#gBamboo)"/>'
        y = 216
        while y > top + 10:
            nx = x + lean * (226 - y) / (226 - top)
            s += f'<path d="M{F(nx - 3.4)} {F(y)}h6.8" stroke="#E4F0C2" stroke-width="1.2" opacity=".8"/>'
            if y < 150 and rnd.random() < .8:
                for _ in range(rnd.randint(2, 4)):
                    side = rnd.choice([1, -1])
                    ang = (20 if side == 1 else 160) + rnd.uniform(-25, 30) * side
                    leaves += leaf(nx, y, rnd.uniform(18, 28), 3.2, ang, rnd.choice(["#4F8B2F", "#64A03C", "#7DB352"]), "#3D6624", .5)
            y -= rnd.uniform(16, 22)
    return s + leaves


def jasmine(seed, flowers=True):
    pal = ("#1E4626", "#2A5C31", "#3B7640", "#5A9459")
    rnd = random.Random(seed)
    s = ground(74, 9, seed)
    s += '<ellipse cx="100" cy="200" rx="64" ry="26" fill="#1E4626"/>'

    def shape(x, y):
        return ell(100, 194, 72, 38)(x, y) and y < 224

    s += canopy(shape, (28, 156, 172, 224), 170, 5, 8, pal, seed, back=.5)
    if flowers:
        for _ in range(30):
            x, y = rnd.uniform(36, 164), rnd.uniform(160, 214)
            if not shape(x, y):
                continue
            r = rnd.uniform(3, 4.2)
            off = rnd.uniform(0, 72)
            for i in range(5):
                a = math.radians(i * 72 + off)
                px, py = x + r * .55 * math.cos(a), y + r * .55 * math.sin(a)
                s += f'<ellipse cx="{F(px)}" cy="{F(py)}" rx="{F(r * .62)}" ry="{F(r * .32)}" fill="#FFFFFF" transform="rotate({F(math.degrees(a))} {F(px)} {F(py)})"/>'
            s += f'<circle cx="{F(x)}" cy="{F(y)}" r=".9" fill="#F7E08A"/>'
    return s


def young(seed, resting=False):
    s = ground(44, 7, seed)
    s += '<path d="M118 226V156" stroke="#C9A36A" stroke-width="3" stroke-linecap="round"/>'
    s += trunk(100, 8, 101, 4, 132)
    s += branch(100, 150, 84, 126, 2.5, 1) + branch(101, 146, 116, 122, 2.5, 1)
    s += canopy(ell(100, 108, 40, 38), (58, 68, 142, 148), 90, 6, 10, AUTUMN if resting else ("#25562B", "#336E36", "#4D8C47", "#7DB765"), seed, back=.5)
    return s


def mound(seed, rx=46, y=222):
    rnd = random.Random(seed)
    s = f'<ellipse cx="100" cy="{y + 5}" rx="{rx + 6}" ry="5" fill="#1B2A12" opacity=".2" filter="url(#soft)"/>'
    s += f'<path d="M{100 - rx} {y + 4}C{100 - rx * .6} {y - 14} {100 + rx * .6} {y - 14} {100 + rx} {y + 4}Z" fill="url(#gSoil)"/>'
    for _ in range(9):
        x = rnd.uniform(100 - rx * .7, 100 + rx * .7)
        s += f'<ellipse cx="{F(x)}" cy="{F(y + rnd.uniform(-5, 2))}" rx="{F(rnd.uniform(1, 2.2))}" ry="{F(rnd.uniform(.7, 1.3))}" fill="{rnd.choice(["#A07A55", "#6B4A2E", "#C2A07A"])}"/>'
    return s + ground(rx + 4, 5, seed + 3, y=y + 3)


def sapling(seed):
    rnd = random.Random(seed)
    s = mound(seed, rx=40)
    s += '<path d="M120 222V138" stroke="#CFA971" stroke-width="2.6" stroke-linecap="round"/>'
    s += '<path d="M100 220C99 196 102 170 100 132" stroke="url(#gStem)" stroke-width="3.2" fill="none" stroke-linecap="round"/>'
    for i, y in enumerate([204, 192, 180, 168, 158, 148, 140]):
        side = 1 if i % 2 == 0 else -1
        ang = (-28 if side == 1 else -152) + rnd.uniform(-8, 8)
        s += leaf(100, y, 22 - i * 1.6, 7.5 - i * .4, ang, "url(#gLeaf)", "#A8D27F", .7)
    return s + leaf(100, 133, 11, 3.6, -70, "#8FC463") + leaf(100, 133, 10, 3.4, -112, "#7CB656")


def sprout(seed):
    s = mound(seed, rx=48)
    s += '<path d="M100 218C99 206 101 196 100 184" stroke="url(#gStem)" stroke-width="3" fill="none" stroke-linecap="round"/>'
    s += '<path d="M100 186C92 174 76 172 70 178C76 190 92 192 100 186Z" fill="url(#gCoty)"/>'
    s += '<path d="M100 184C108 170 124 168 130 174C124 186 108 190 100 184Z" fill="url(#gCoty)"/>'
    s += leaf(100, 184, 13, 4, -88, "#5E9E3C", "#A8D27F", .6)
    return s + '<circle cx="124" cy="173" r="2.2" fill="#E6F6FF" opacity=".85"/>'


def seed_mound(seed):
    s = '<circle cx="100" cy="206" r="30" fill="url(#gSeedGlow)"/>'
    s += mound(seed, rx=50)
    s += '<ellipse cx="100" cy="212" rx="9" ry="6.4" fill="url(#gSeed)" transform="rotate(-18 100 212)"/>'
    s += '<path d="M96 208q-3 -2 -2 -6" stroke="#F4F0D8" stroke-width="1.2" fill="none" stroke-linecap="round"/>'
    return s + '<path d="M86 220C92 214 108 214 114 220Z" fill="#8C6239"/>'


def lotus_pond():
    s = '<ellipse cx="100" cy="186" rx="96" ry="34" fill="url(#gWater)"/>'
    s += '<ellipse cx="100" cy="182" rx="88" ry="26" fill="none" stroke="#fff" stroke-width="1" opacity=".25"/>'
    for i in range(5):
        s += f'<path d="M{40 + i * 28} {176 + (i % 2) * 10}h{12 + i % 3 * 6}" stroke="#fff" stroke-width="1.2" opacity=".35" stroke-linecap="round"/>'
    for x, y, r in [(46, 190, 13), (78, 202, 11), (130, 198, 12), (154, 182, 10), (110, 172, 9)]:
        s += f'<path d="M{x} {y}L{F(x + r)} {F(y - r * .25)}A{r} {F(r * .42)} 0 1 1 {F(x + r * .95)} {F(y + r * .1)}Z" fill="url(#gPad)"/>'
    for x, y, k in [(84, 194, 1), (138, 186, .8)]:
        for a in (-62, -32, 32, 62, 0):
            s += f'<path d="M{x} {y}c-3 -6 -3 -14 0 -18c3 4 3 12 0 18z" fill="url(#gPetal)" transform="rotate({F(a * k)} {x} {y})"/>'
        s += f'<ellipse cx="{x}" cy="{y - 2}" rx="3" ry="1.6" fill="#F7D35B"/>'
    return s + '<path d="M118 178q1 -10 3 -12" stroke="#4E8A3A" stroke-width="1.2" fill="none"/><ellipse cx="121" cy="164" rx="2.6" ry="5" fill="#F48FB1"/>'


DEFS = """
<filter id="soft" x="-30%" y="-80%" width="160%" height="260%"><feGaussianBlur stdDeviation="2.4"/></filter>
<linearGradient id="gBark" x1="0" x2="1"><stop offset="0" stop-color="#9A7050"/><stop offset=".55" stop-color="#6E4B30"/><stop offset="1" stop-color="#4B3220"/></linearGradient>
<linearGradient id="gPalm" x1="0" x2="1"><stop offset="0" stop-color="#B08D63"/><stop offset="1" stop-color="#6E5236"/></linearGradient>
<linearGradient id="gBamboo" x1="0" x2="1"><stop offset="0" stop-color="#9CC45C"/><stop offset=".5" stop-color="#7AA846"/><stop offset="1" stop-color="#557D2E"/></linearGradient>
<linearGradient id="gStem" x1="0" y1="1" x2="0" y2="0"><stop offset="0" stop-color="#7A5A36"/><stop offset=".35" stop-color="#5E8B37"/><stop offset="1" stop-color="#7DB552"/></linearGradient>
<linearGradient id="gLeaf" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#8CC560"/><stop offset="1" stop-color="#3F7F2E"/></linearGradient>
<linearGradient id="gCoty" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#A6D877"/><stop offset="1" stop-color="#5E9E3C"/></linearGradient>
<radialGradient id="gSoil" cx=".45" cy=".2" r=".9"><stop offset="0" stop-color="#9A6E45"/><stop offset=".6" stop-color="#7A5233"/><stop offset="1" stop-color="#5A3B22"/></radialGradient>
<radialGradient id="gSeed" cx=".35" cy=".3" r=".8"><stop offset="0" stop-color="#C08A55"/><stop offset="1" stop-color="#6B4320"/></radialGradient>
<radialGradient id="gSeedGlow"><stop offset="0" stop-color="#FFE9A8" stop-opacity=".55"/><stop offset="1" stop-color="#FFE9A8" stop-opacity="0"/></radialGradient>
<linearGradient id="gMango" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#FAD25A"/><stop offset=".6" stop-color="#F2A43A"/><stop offset="1" stop-color="#DD6A2C"/></linearGradient>
<radialGradient id="gNut" cx=".35" cy=".3"><stop offset="0" stop-color="#8FA24A"/><stop offset="1" stop-color="#4E5A22"/></radialGradient>
<linearGradient id="gWater" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#8ED0E0"/><stop offset="1" stop-color="#4E9DB8"/></linearGradient>
<linearGradient id="gPad" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#6FB25A"/><stop offset="1" stop-color="#2F7A3A"/></linearGradient>
<linearGradient id="gPetal" x1="0" y1="1" x2="0" y2="0"><stop offset="0" stop-color="#F7C6D6"/><stop offset="1" stop-color="#E7709A"/></linearGradient>
"""

SPECIES_ORDER = ["NEEM", "MANGO", "GULMOHAR", "PEEPAL", "COCONUT", "JASMINE", "BANYAN", "BAMBOO"]
STAGE_ORDER = ["SEED", "SPROUT", "SAPLING", "YOUNG", "GROWN"]
# Seasons as in the app's Species enum: months it flowers or fruits; mango flowers Jan–Mar, fruits Apr–Jun.
SEASONS = {"NEEM": {3, 4}, "MANGO": {1, 2, 3, 4, 5, 6}, "GULMOHAR": {4, 5, 6}, "PEEPAL": {4, 5, 6},
           "COCONUT": {9, 10, 11}, "JASMINE": set(range(3, 11)), "BANYAN": {5, 6, 7}, "BAMBOO": set()}
# Small stages drawn closer, so a seed is still a seed at icon size (the app's cropFor).
CROPS = {"SEED": "40 168 120 66", "SPROUT": "40 150 120 84", "SAPLING": "40 110 120 124"}


def tree_body(species, stage, variant, month, resting):
    """One tree's drawing (no <svg> around it)."""
    s = (variant * 7919 + SPECIES_ORDER.index(species) * 104729 + STAGE_ORDER.index(stage)) % 2_147_483_647
    if stage == "SEED":
        return seed_mound(s)
    if stage == "SPROUT":
        return sprout(s)
    if stage == "SAPLING":
        return sapling(s)
    if stage == "YOUNG":
        return young(s, resting) + (fallen(s) if resting else "")
    in_season = month in SEASONS[species]
    if species == "NEEM":
        return neem(s, resting, flowers=in_season)
    if species == "MANGO":
        return mango(s, resting, fruit=in_season and month >= 4, flowers=in_season and month < 4)
    if species == "GULMOHAR":
        return gulmohar(s, resting, bloom=in_season)
    if species == "BANYAN":
        return banyan(s, resting)
    if species == "PEEPAL":
        return peepal(s, resting)
    if species == "COCONUT":
        return coconut(s)
    if species == "BAMBOO":
        return bamboo(s)
    return jasmine(s, flowers=in_season)


@lru_cache(maxsize=512)
def tree_svg(species, stage, variant, month, resting, crop=False):
    """A whole SVG file for one tree. `crop`: small stages close up (for lists and icons)."""
    vb = CROPS.get(stage, "0 0 200 240") if crop else "0 0 200 240"
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vb}" preserveAspectRatio="xMidYMax meet">'
            f"<defs>{DEFS}</defs>{tree_body(species, stage, variant, month, resting)}</svg>")


@lru_cache(maxsize=1)
def pond_svg():
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 240"><defs>{DEFS}</defs>{lotus_pond()}</svg>'


# ------------------------------------------------------------------ the scene's living bits (fragments)
SCENE_DEFS = """
<linearGradient id="fMonarch" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#FDBA4D"/><stop offset="1" stop-color="#E8641F"/></linearGradient>
<linearGradient id="fMorpho" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#7CC4FF"/><stop offset="1" stop-color="#2563EB"/></linearGradient>
<linearGradient id="fYellow" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#FFF08A"/><stop offset="1" stop-color="#F2C21D"/></linearGradient>
<radialGradient id="fFly"><stop offset="0" stop-color="#FFF6B0" stop-opacity=".95"/><stop offset=".35" stop-color="#FFE47A" stop-opacity=".45"/><stop offset="1" stop-color="#FFE47A" stop-opacity="0"/></radialGradient>
<radialGradient id="fSun"><stop offset="0" stop-color="#FFF6D8"/><stop offset=".25" stop-color="#FFE7A3" stop-opacity=".9"/><stop offset="1" stop-color="#FFE7A3" stop-opacity="0"/></radialGradient>
<radialGradient id="fMoon"><stop offset="0" stop-color="#F7F1DC" stop-opacity=".55"/><stop offset="1" stop-color="#F7F1DC" stop-opacity="0"/></radialGradient>
"""


def butterfly(kind, x, y, size, dur, path_scale=1.0, begin=0.0):
    """A butterfly looping around (x, y), wings flapping."""
    fill, edge, dot = {
        0: ("url(#fMonarch)", "#1F1712", "#FFFFFF"),
        1: ("url(#fMorpho)", "#0F1E3D", "#DCEBFF"),
        2: ("url(#fYellow)", "#5A4510", "#FFF9DB"),
    }[kind % 3]
    w = (f'<path d="M0 -1C4 -10 14 -15 19 -11C22 -7 17 1 2 2Z" fill="{fill}" stroke="{edge}" stroke-width="1"/>'
         f'<path d="M1 2C9 3 15 8 12 13C9 16 3 12 0 4Z" fill="{fill}" stroke="{edge}" stroke-width="1"/>'
         f'<circle cx="16" cy="-9" r=".9" fill="{dot}"/><circle cx="11" cy="12" r=".8" fill="{dot}"/>')
    flap = 'keyTimes="0;.55;.78;1" dur=".7s" repeatCount="indefinite"'
    k = size / 44
    r = 26 * path_scale
    loop = f"M{F(x - r)} {F(y)}c{F(r * .6)} {F(-r)} {F(r * 1.6)} {F(-r * .4)} {F(r * 1.4)} {F(r * .4)}s{F(-r * 1.6)} {F(r * .5)} {F(-r * 1.4)} {F(-r * .4)}"
    return (f'<g><animateMotion path="{loop}" dur="{F(dur)}s" begin="{F(begin)}s" repeatCount="indefinite"/><g transform="scale({k:.3f})">'
            f'<g><animateTransform attributeName="transform" type="scale" values="1 1;1 1;.3 1;1 1" {flap}/>{w}</g>'
            f'<g transform="scale(-1 1)"><animateTransform attributeName="transform" type="scale" values="-1 1;-1 1;-.3 1;-1 1" {flap}/>{w}</g>'
            f'<ellipse cx="0" cy="1" rx="1.3" ry="6" fill="{edge}"/></g></g>')


def fireflies(n, box, seed):
    rnd = random.Random(seed)
    x0, y0, x1, y1 = box
    s = ""
    for _ in range(n):
        x, y = rnd.uniform(x0, x1), rnd.uniform(y0, y1)
        dur = rnd.uniform(2.2, 4.6)
        dx, dy = rnd.uniform(-14, 14), rnd.uniform(-10, 6)
        s += (f'<g><animateTransform attributeName="transform" type="translate" values="0 0;{F(dx)} {F(dy)};0 0" dur="{F(dur * 2.5)}s" repeatCount="indefinite"/>'
              f'<circle cx="{F(x)}" cy="{F(y)}" r="{F(rnd.uniform(7, 12))}" fill="url(#fFly)"><animate attributeName="opacity" values=".15;1;.35;.9;.15" dur="{F(dur)}s" begin="{F(rnd.uniform(0, 3))}s" repeatCount="indefinite"/></circle>'
              f'<circle cx="{F(x)}" cy="{F(y)}" r="1.6" fill="#FFF8C8"/></g>')
    return s


def tufts(n, box, seed, h=(4, 9)):
    rnd = random.Random(seed)
    x0, y0, x1, y1 = box
    cols = ["#5F9A45", "#6FA84E", "#7FB55A", "#4E8A3A"]
    paths = {c: "" for c in cols}
    for _ in range(n):
        x, y = rnd.uniform(x0, x1), rnd.uniform(y0, y1)
        k = (y - y0) / max(1, (y1 - y0))
        hh = rnd.uniform(*h) * (.6 + .8 * k)
        paths[rnd.choice(cols)] += f"M{F(x)} {F(y)}q-1.5 -{F(hh * .6)} -3 -{F(hh)}M{F(x)} {F(y)}q.5 -{F(hh * .7)} 1 -{F(hh * 1.1)}M{F(x)} {F(y)}q2 -{F(hh * .5)} 4 -{F(hh * .8)}"
    return "".join(f'<path d="{d}" stroke="{c}" stroke-width="1.2" fill="none" stroke-linecap="round"/>' for c, d in paths.items() if d)


def wildflowers(n, box, seed):
    rnd = random.Random(seed)
    x0, y0, x1, y1 = box
    cols = ["#FFFFFF", "#FDE68A", "#F9A8D4", "#C4B5FD"]
    return "".join(f'<circle cx="{F(rnd.uniform(x0, x1))}" cy="{F(rnd.uniform(y0, y1))}" r="{F(rnd.uniform(1, 2))}" fill="{rnd.choice(cols)}"/>' for _ in range(n))


def cloud(x, y, s, op=.9, color="#fff"):
    return (f'<g opacity="{op}"><animateTransform attributeName="transform" type="translate" values="0 0;24 0;0 0" dur="60s" repeatCount="indefinite"/>'
            f'<ellipse cx="{F(x)}" cy="{F(y)}" rx="{F(30 * s)}" ry="{F(9 * s)}" fill="{color}"/><ellipse cx="{F(x + 14 * s)}" cy="{F(y - 7 * s)}" rx="{F(17 * s)}" ry="{F(11 * s)}" fill="{color}"/>'
            f'<ellipse cx="{F(x - 12 * s)}" cy="{F(y - 4 * s)}" rx="{F(13 * s)}" ry="{F(8 * s)}" fill="{color}"/></g>')


def bird(x, y, s=1.0, dur=14):
    return (f'<path d="M{F(x)} {F(y)}q{F(3 * s)} {F(-3 * s)} {F(6 * s)} 0q{F(3 * s)} {F(-3 * s)} {F(6 * s)} 0" stroke="#3B4A3A" stroke-width="1.3" fill="none" stroke-linecap="round">'
            f'<animateTransform attributeName="transform" type="translate" values="0 0;40 -6;80 0" dur="{dur}s" repeatCount="indefinite"/></path>')


def falling_leaf(x, y, dur=9, col="#C99B45"):
    return (f'<g><animateMotion path="M{F(x)} {F(y)}c10 20 -10 40 4 60c12 18 -6 36 6 56" dur="{dur}s" repeatCount="indefinite"/>'
            f'<path d="M0 0q4 -3 8 0q-4 3 -8 0z" fill="{col}"><animateTransform attributeName="transform" type="rotate" values="0;180;360" dur="3s" repeatCount="indefinite"/></path></g>')


def rain(width, height, seed):
    rnd = random.Random(seed)
    lines = "".join(f"M{F(rnd.uniform(0, width + 20))} {F(rnd.uniform(-height, height))}l-3 9" for _ in range(int(width / 4)))
    return (f'<g stroke="#E8F1F6" stroke-width="1" opacity=".6"><path d="{lines}"/>'
            f'<animateTransform attributeName="transform" type="translate" values="0 0;-30 {height}" dur="1.6s" repeatCount="indefinite"/></g>')
