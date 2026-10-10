"""Crisp, anti-aliased icons drawn from simple vector paths (no image files to ship).

Every icon lives on a 24 x 24 grid and is rendered with Pillow at 4x size and shrunk
back, so it looks the same on every computer. Icons are single-colour; ``render`` gives
a PIL image and ``photo`` a cached Tk image.

Path syntax is the usual SVG subset: M L H V C Q A Z (upper/lower case).
"""
import math
import re

from PIL import Image, ImageChops, ImageDraw

SS = 4                                   # supersampling factor
_TOKEN = re.compile(r"[MmLlHhVvCcQqAaZz]|-?(?:\d+\.?\d*|\.\d+)(?:e-?\d+)?")
_ARGS = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "Q": 4, "A": 7, "Z": 0}


# --------------------------------------------------------------------------- #
# path -> polylines
# --------------------------------------------------------------------------- #
def _arc_points(x1, y1, rx, ry, phi, fa, fs, x2, y2, steps=24):
    """SVG endpoint arc -> points (implementation notes F.6.5 of the SVG spec)."""
    if rx == 0 or ry == 0 or (x1 == x2 and y1 == y2):
        return [(x2, y2)]
    rx, ry = abs(rx), abs(ry)
    cp, sp = math.cos(math.radians(phi)), math.sin(math.radians(phi))
    dx, dy = (x1 - x2) / 2, (y1 - y2) / 2
    x1p, y1p = cp * dx + sp * dy, -sp * dx + cp * dy
    lam = (x1p ** 2) / (rx ** 2) + (y1p ** 2) / (ry ** 2)
    if lam > 1:
        rx, ry = rx * math.sqrt(lam), ry * math.sqrt(lam)
    num = rx ** 2 * ry ** 2 - rx ** 2 * y1p ** 2 - ry ** 2 * x1p ** 2
    den = rx ** 2 * y1p ** 2 + ry ** 2 * x1p ** 2
    coef = math.sqrt(max(0.0, num / den)) * (-1 if fa == fs else 1)
    cxp, cyp = coef * rx * y1p / ry, -coef * ry * x1p / rx
    cx, cy = cp * cxp - sp * cyp + (x1 + x2) / 2, sp * cxp + cp * cyp + (y1 + y2) / 2

    def ang(ux, uy, vx, vy):
        a = math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)
        return a

    th1 = ang(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    dth = ang((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not fs and dth > 0:
        dth -= 2 * math.pi
    elif fs and dth < 0:
        dth += 2 * math.pi
    pts = []
    for i in range(1, steps + 1):
        t = th1 + dth * i / steps
        px, py = rx * math.cos(t), ry * math.sin(t)
        pts.append((cp * px - sp * py + cx, sp * px + cp * py + cy))
    return pts


def parse(d):
    """Return [(points, closed)] for an SVG-style path string."""
    tokens = _TOKEN.findall(d)
    subs, cur, closed = [], [], False
    x = y = sx = sy = 0.0
    i, cmd = 0, None

    def flush():
        nonlocal cur, closed
        if len(cur) > 1:
            subs.append((cur, closed))
        cur, closed = [], False

    while i < len(tokens):
        if tokens[i].isalpha():
            cmd = tokens[i]
            i += 1
            if cmd in "Zz":
                if cur:
                    closed = True
                    flush()
                x, y = sx, sy
                continue
        up, rel = cmd.upper(), cmd.islower()
        n = _ARGS[up]
        a = [float(t) for t in tokens[i:i + n]]
        i += n
        ox, oy = (x, y) if rel else (0.0, 0.0)
        if up == "M":
            flush()
            x, y = a[0] + ox, a[1] + oy
            sx, sy = x, y
            cur = [(x, y)]
            cmd = "l" if rel else "L"                    # extra pairs after M are lines
        elif up == "L":
            x, y = a[0] + ox, a[1] + oy
            cur.append((x, y))
        elif up == "H":
            x = a[0] + (x if rel else 0)
            cur.append((x, y))
        elif up == "V":
            y = a[0] + (y if rel else 0)
            cur.append((x, y))
        elif up == "C":
            p1, p2, p3 = (a[0] + ox, a[1] + oy), (a[2] + ox, a[3] + oy), (a[4] + ox, a[5] + oy)
            for k in range(1, 21):
                t = k / 20
                u = 1 - t
                cur.append((u ** 3 * x + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0],
                            u ** 3 * y + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1]))
            x, y = p3
        elif up == "Q":
            p1, p2 = (a[0] + ox, a[1] + oy), (a[2] + ox, a[3] + oy)
            for k in range(1, 17):
                t = k / 16
                u = 1 - t
                cur.append((u * u * x + 2 * u * t * p1[0] + t * t * p2[0],
                            u * u * y + 2 * u * t * p1[1] + t * t * p2[1]))
            x, y = p2
        elif up == "A":
            ex, ey = a[5] + ox, a[6] + oy
            cur.extend(_arc_points(x, y, a[0], a[1], a[2], int(a[3]), int(a[4]), ex, ey))
            x, y = ex, ey
    flush()
    return subs


# --------------------------------------------------------------------------- #
# icon definitions: ("path", d, mode) / ("circle", cx, cy, r, mode); mode: s=stroke f=fill sf=both c=cut out
# --------------------------------------------------------------------------- #
def _gear():
    """Outline of a 8-tooth cog centred on (12,12)."""
    pts, teeth = [], 8
    ro, ri = 10.0, 7.6
    for t in range(teeth):
        base = 2 * math.pi * t / teeth
        for frac, r in ((-0.20, ri), (-0.12, ro), (0.12, ro), (0.20, ri)):
            a = base + frac * 2 * math.pi / teeth * 1.25
            pts.append((12 + r * math.cos(a), 12 + r * math.sin(a)))
    return "M" + " L".join(f"{x:.2f} {y:.2f}" for x, y in pts) + " Z"


PHONE = ("M22 16.92 v3 a2 2 0 0 1 -2.18 2 19.79 19.79 0 0 1 -8.63 -3.07 19.5 19.5 0 0 1 -6 -6 "
         "19.79 19.79 0 0 1 -3.07 -8.67 A2 2 0 0 1 4.11 2 h3 a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 "
         "2 2 0 0 1 -.45 2.11 L8.09 9.91 a16 16 0 0 0 6 6 l1.27 -1.27 a2 2 0 0 1 2.11 -.45 "
         "12.84 12.84 0 0 0 2.81 .7 A2 2 0 0 1 22 16.92 z")
VIDEO_BODY = "M3 5 H14 A2 2 0 0 1 16 7 V17 A2 2 0 0 1 14 19 H3 A2 2 0 0 1 1 17 V7 A2 2 0 0 1 3 5 Z"
BELL = "M18 8 A6 6 0 0 0 6 8 c0 7 -3 9 -3 9 h18 s-3 -2 -3 -9"
BELL_CLAPPER = "M13.73 21 a2 2 0 0 1 -3.46 0"
USER_BODY = "M20 21 v-2 a4 4 0 0 0 -4 -4 H8 a4 4 0 0 0 -4 4 v2"

ICONS = {
    "logo": [("path", "M7 3 H17 Q21 3 21 7 V13 Q21 17 17 17 H10 L5.5 21 V17 Q3 17 3 13 V7 Q3 3 7 3 Z", "s"),
             ("circle", 8.4, 10, 1.15, "f"), ("circle", 12, 10, 1.15, "f"), ("circle", 15.6, 10, 1.15, "f")],
    "chat": [("path", "M7 3.5 H17 Q20.5 3.5 20.5 7 V13 Q20.5 16.5 17 16.5 H10.5 L6 20.5 V16.5 Q3.5 16.5 3.5 13 V7 "
                      "Q3.5 3.5 7 3.5 Z", "s")],
    "chat_fill": [("path", "M7 3.5 H17 Q20.5 3.5 20.5 7 V13 Q20.5 16.5 17 16.5 H10.5 L6 20.5 V16.5 Q3.5 16.5 3.5 13 V7 "
                           "Q3.5 3.5 7 3.5 Z", "sf")],
    "user": [("path", USER_BODY, "s"), ("circle", 12, 7, 4, "s")],
    "users": [("path", "M17 21 v-2 a4 4 0 0 0 -4 -4 H5 a4 4 0 0 0 -4 4 v2", "s"), ("circle", 9, 7, 4, "s"),
              ("path", "M23 21 v-2 a4 4 0 0 0 -3 -3.87", "s"), ("path", "M16 3.13 a4 4 0 0 1 0 7.75", "s")],
    "users_fill": [("path", "M17 21 v-2 a4 4 0 0 0 -4 -4 H5 a4 4 0 0 0 -4 4 v2 Z", "sf"), ("circle", 9, 7, 4, "sf"),
                   ("path", "M23 21 v-2 a4 4 0 0 0 -3 -3.87", "s"), ("path", "M16 3.13 a4 4 0 0 1 0 7.75", "s")],
    "user_plus": [("path", "M16 21 v-2 a4 4 0 0 0 -4 -4 H5 a4 4 0 0 0 -4 4 v2", "s"), ("circle", 8.5, 7, 4, "s"),
                  ("path", "M20 8 v6 M17 11 h6", "s")],
    "phone": [("path", PHONE, "s")],
    "phone_fill": [("path", PHONE, "sf")],
    "video": [("path", VIDEO_BODY, "s"), ("path", "M23 7 L16 12 L23 17 Z", "s")],
    "video_fill": [("path", VIDEO_BODY, "sf"), ("path", "M23 7 L16 12 L23 17 Z", "sf")],
    "search": [("circle", 11, 11, 8, "s"), ("path", "M21 21 L16.65 16.65", "s")],
    "settings": [("path", _gear(), "s"), ("circle", 12, 12, 3.2, "s")],
    "menu": [("path", "M3 6 H21 M3 12 H17 M3 18 H12", "s")],
    "chevron_up": [("path", "M6 15 L12 9 L18 15", "s")],
    "chevron_down": [("path", "M6 9 L12 15 L18 9", "s")],
    "chevron_left": [("path", "M15 18 L9 12 L15 6", "s")],
    "chevron_right": [("path", "M9 18 L15 12 L9 6", "s")],
    "dots_v": [("circle", 12, 5, 1.9, "f"), ("circle", 12, 12, 1.9, "f"), ("circle", 12, 19, 1.9, "f")],
    "dots_h": [("circle", 5, 12, 1.9, "f"), ("circle", 12, 12, 1.9, "f"), ("circle", 19, 12, 1.9, "f")],
    "info": [("circle", 12, 12, 10, "f"), ("path", "M12 16.5 V11", "c"), ("circle", 12, 7.8, 1.2, "c")],
    "smile": [("circle", 12, 12, 10, "s"), ("path", "M8 14 C9 15.6 10.5 16.2 12 16.2 C13.5 16.2 15 15.6 16 14", "s"),
              ("circle", 9, 9.2, 0.9, "f"), ("circle", 15, 9.2, 0.9, "f")],
    "paperclip": [("path", "M21.44 11.05 l-9.19 9.19 a6 6 0 0 1 -8.49 -8.49 l9.19 -9.19 a4 4 0 0 1 5.66 5.66 "
                           "l-9.2 9.19 a2 2 0 0 1 -2.83 -2.83 l8.49 -8.48", "s")],
    "image": [("path", "M5 3 H19 A2 2 0 0 1 21 5 V19 A2 2 0 0 1 19 21 H5 A2 2 0 0 1 3 19 V5 A2 2 0 0 1 5 3 Z", "s"),
              ("circle", 8.5, 8.5, 1.6, "s"), ("path", "M21 15 L16 10 L5 21", "s")],
    "send": [("path", "M22 2 L15 22 L11 13 L2 9 Z", "sf"), ("path", "M22 2 L11 13", "c")],
    "bell": [("path", BELL, "s"), ("path", BELL_CLAPPER, "s")],
    "bell_off": [("path", BELL, "s"), ("path", BELL_CLAPPER, "s"), ("path", "M3 3 L21 21", "s")],
    "trash": [("path", "M3 6 H21", "s"),
              ("path", "M19 6 v14 a2 2 0 0 1 -2 2 H7 a2 2 0 0 1 -2 -2 V6 m3 0 V4 a2 2 0 0 1 2 -2 h4 a2 2 0 0 1 2 2 v2",
               "s"),
              ("path", "M10 11 v6 M14 11 v6", "s")],
    "ban": [("circle", 12, 12, 10, "s"), ("path", "M4.93 4.93 L19.07 19.07", "s")],
    "link": [("path", "M10 13 a5 5 0 0 0 7.54 .54 l3 -3 a5 5 0 0 0 -7.07 -7.07 l-1.72 1.71", "s"),
             ("path", "M14 11 a5 5 0 0 0 -7.54 -.54 l-3 3 a5 5 0 0 0 7.07 7.07 l1.71 -1.71", "s")],
    "image_fill": [("path", "M5 3 H19 A2 2 0 0 1 21 5 V19 A2 2 0 0 1 19 21 H5 A2 2 0 0 1 3 19 V5 A2 2 0 0 1 5 3 Z", "f"),
                   ("circle", 8.5, 8.5, 1.8, "c"), ("path", "M21 15 L16 10 L5 21", "c")],
    "file": [("path", "M14 2 H6 a2 2 0 0 0 -2 2 v16 a2 2 0 0 0 2 2 h12 a2 2 0 0 0 2 -2 V8 Z", "s"),
             ("path", "M14 2 v6 h6", "s")],
    "check": [("path", "M20 6 L9 17 L4 12", "s")],
    "check2": [("path", "M1.5 12.5 L6 17 L15.5 6.5", "s"), ("path", "M10 15 L12 17 L21.5 6.5", "s")],
    "plus": [("path", "M12 5 V19 M5 12 H19", "s")],
    "x": [("path", "M18 6 L6 18 M6 6 L18 18", "s")],
    "camera": [("path", "M23 19 a2 2 0 0 1 -2 2 H3 a2 2 0 0 1 -2 -2 V8 a2 2 0 0 1 2 -2 h4 l2 -3 h6 l2 3 h4 a2 2 0 0 1 2 2 z",
                "s"), ("circle", 12, 13, 4, "s")],
    "pencil": [("path", "M12 20 h9", "s"), ("path", "M16.5 3.5 a2.12 2.12 0 0 1 3 3 L7 19 l-4 1 1 -4 L16.5 3.5 z", "s")],
    "logout": [("path", "M9 21 H5 a2 2 0 0 1 -2 -2 V5 a2 2 0 0 1 2 -2 h4", "s"), ("path", "M16 17 l5 -5 -5 -5", "s"),
               ("path", "M21 12 H9", "s")],
    "lock": [("path", "M5 11 H19 a2 2 0 0 1 2 2 v7 a2 2 0 0 1 -2 2 H5 a2 2 0 0 1 -2 -2 v-7 a2 2 0 0 1 2 -2 Z", "s"),
             ("path", "M7 11 V7 a5 5 0 0 1 10 0 v4", "s")],
    "wallpaper": [("path", "M5 3 H19 A2 2 0 0 1 21 5 V19 A2 2 0 0 1 19 21 H5 A2 2 0 0 1 3 19 V5 A2 2 0 0 1 5 3 Z", "s"),
                  ("circle", 8.5, 8.5, 1.1, "f"), ("circle", 15.5, 8.5, 1.1, "f"),
                  ("circle", 8.5, 15.5, 1.1, "f"), ("circle", 15.5, 15.5, 1.1, "f")],
}


def _draw_poly(draw, pts, width, closed):
    pts = list(pts)
    if closed:
        pts.append(pts[0])
    if len(pts) > 1:
        draw.line(pts, fill=255, width=max(1, int(round(width))), joint="curve")
    r = width / 2
    for end in (pts[0], pts[-1]):                    # round caps
        draw.ellipse([end[0] - r, end[1] - r, end[0] + r, end[1] + r], fill=255)


def render(name, size, color, stroke=2.0):
    """The icon as an RGBA PIL image of size x size pixels, in ``color`` (hex)."""
    S = size * SS
    k = S / 24.0
    layers = {"ink": Image.new("L", (S, S), 0), "cut": Image.new("L", (S, S), 0)}
    draws = {key: ImageDraw.Draw(im) for key, im in layers.items()}
    w = stroke * k
    for item in ICONS[name]:
        if item[0] == "path":
            _, d, mode = item
            target = draws["cut" if mode == "c" else "ink"]
            for pts, closed in parse(d):
                scaled = [(px * k, py * k) for px, py in pts]
                if "f" in mode and len(scaled) > 2:
                    target.polygon(scaled, fill=255)
                if "s" in mode or mode == "c":
                    _draw_poly(target, scaled, w, closed)
        else:
            _, cx, cy, r, mode = item
            target = draws["cut" if mode == "c" else "ink"]
            grow = w / 2 if "s" in mode else 0          # a stroke is centred on the circle's edge
            box = [(cx - r) * k - grow, (cy - r) * k - grow, (cx + r) * k + grow, (cy + r) * k + grow]
            if "f" in mode or mode == "c":
                target.ellipse(box, fill=255)
            elif "s" in mode:
                target.ellipse(box, outline=255, width=max(1, int(round(w))))
    alpha = ImageChops.subtract(layers["ink"], layers["cut"]).resize((size, size), Image.LANCZOS)
    img = Image.new("RGBA", (size, size), color)
    img.putalpha(alpha)
    return img
