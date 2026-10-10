"""Image helpers built on Pillow: profile pictures, rounded shapes, gradients, bubbles, wallpapers.

Shapes are drawn at 4x size and shrunk back, which gives smooth (anti-aliased) edges that
Tkinter's own canvas shapes cannot. Nothing in here needs a window, so it is easy to test.
"""
import base64
import hashlib
import io
import os
import random

from PIL import Image, ImageDraw, ImageFont, ImageOps

from client import icons
from shared import protocol as P

SS = 4
AVATAR_PX = 256                 # pictures are stored/sent as 256 x 256 JPEG
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


class AvatarError(Exception):
    """The chosen file cannot be used as a profile picture (message is user-friendly)."""


def hex_rgb(color):
    color = color.lstrip("#")
    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))


def mix(c1, c2, t):
    a, b = hex_rgb(c1), hex_rgb(c2)
    return "#%02x%02x%02x" % tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


# --------------------------------------------------------------------------- #
# profile pictures
# --------------------------------------------------------------------------- #
def prepare_avatar(path, size=AVATAR_PX):
    """Read any common image file and return it as base64 JPEG: square, centred, <= the server limit."""
    try:
        if os.path.getsize(path) > MAX_UPLOAD_BYTES:
            raise AvatarError("That file is too big. Choose a picture under 25 MB.")
        with Image.open(path) as src:
            src = ImageOps.exif_transpose(src)
            if src.mode in ("RGBA", "LA", "P"):
                src = src.convert("RGBA")
                flat = Image.new("RGB", src.size, "#ffffff")
                flat.paste(src, mask=src.getchannel("A"))
                src = flat
            else:
                src = src.convert("RGB")
            square = ImageOps.fit(src, (size, size), Image.LANCZOS, centering=(0.5, 0.4))
    except AvatarError:
        raise
    except Exception as exc:                     # not an image, corrupt, unsupported format ...
        raise AvatarError("That file could not be opened as a picture.") from exc
    for quality in (90, 80, 70, 55):
        buf = io.BytesIO()
        square.save(buf, "JPEG", quality=quality, optimize=True)
        data = base64.b64encode(buf.getvalue()).decode("ascii")
        if len(data) <= P.MAX_AVATAR_CHARS:
            return data
    raise AvatarError("That picture is too detailed to use. Try a simpler one.")


def decode_avatar(b64):
    """base64 picture -> RGB PIL image, or None if it is damaged."""
    try:
        im = Image.open(io.BytesIO(base64.b64decode(b64)))
        im.load()
        return im.convert("RGB")
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# basic shapes
# --------------------------------------------------------------------------- #
def _canvas(w, h):
    return Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))


def _done(im, w, h):
    return im.resize((w, h), Image.LANCZOS)


def rounded_rect(w, h, r, fill, outline=None, ow=1):
    im = _canvas(w, h)
    d = ImageDraw.Draw(im)
    box = [0, 0, w * SS - 1, h * SS - 1]
    d.rounded_rectangle(box, radius=r * SS, fill=fill, outline=outline, width=ow * SS if outline else 0)
    return _done(im, w, h)


def circle_shape(d, fill=None, outline=None, ow=1):
    im = _canvas(d, d)
    ImageDraw.Draw(im).ellipse([0, 0, d * SS - 1, d * SS - 1], fill=fill, outline=outline,
                               width=ow * SS if outline else 0)
    return _done(im, d, d)


def gradient_rrect(w, h, r, left, right, outline=None, ow=1):
    """Rounded rectangle whose colour fades from ``left`` to ``right`` (horizontal)."""
    grad = Image.new("RGB", (w * SS, h * SS))
    gd = ImageDraw.Draw(grad)
    for x in range(w * SS):
        gd.line([(x, 0), (x, h * SS)], fill=mix(left, right, x / max(1, w * SS - 1)))
    mask = Image.new("L", (w * SS, h * SS), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w * SS - 1, h * SS - 1], radius=r * SS, fill=255)
    out = grad.convert("RGBA")
    out.putalpha(mask)
    if outline:
        ImageDraw.Draw(out).rounded_rectangle([0, 0, w * SS - 1, h * SS - 1], radius=r * SS,
                                              outline=outline, width=ow * SS)
    return _done(out, w, h)


def bubble(w, h, fill, tail=False, r=18, tail_w=9):
    """A chat bubble ``w`` x ``h``; with ``tail`` a small tail sticks out at the bottom-right
    (the image is then ``tail_w`` pixels wider than the bubble body)."""
    tw = tail_w if tail else 0
    im = _canvas(w + tw, h)
    d = ImageDraw.Draw(im)
    r = min(r, h // 2, w // 2)
    d.rounded_rectangle([0, 0, w * SS - 1, h * SS - 1], radius=r * SS, fill=fill)
    if tail:
        k = SS
        pts = [((w - 22) * k, (h - 22) * k), ((w - 1) * k, (h - 22) * k)]
        p0, p1, p2 = (w - 1, h - 16), (w - 1, h - 3), (w + tw, h - 0.5)      # right edge flares out
        for step in range(0, 13):
            t = step / 12
            u = 1 - t
            pts.append(((u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0]) * k,
                        (u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1]) * k))
        pts += [((w - 22) * k, h * k - 1)]
        d.polygon(pts, fill=fill)
    return _done(im, w + tw, h)


# --------------------------------------------------------------------------- #
# avatars as images
# --------------------------------------------------------------------------- #
_AVATAR_COLORS = ["#2563eb", "#7c3aed", "#db2777", "#ea580c", "#0d9488", "#4f46e5", "#0891b2", "#c026d3"]
_FONT_FILES = ("segoeuib.ttf", "seguisb.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf", "FreeSansBold.ttf",
               "Helvetica.ttc", "Arial Bold.ttf")


def _font(px):
    for name in _FONT_FILES:
        try:
            return ImageFont.truetype(name, px)
        except OSError:
            continue
    try:
        return ImageFont.load_default(px)
    except TypeError:                              # very old Pillow
        return ImageFont.load_default()


def initials(name):
    parts = [p for p in (name or "?").replace("_", " ").split() if p]
    if not parts:
        return "?"
    letters = parts[0][0] + (parts[-1][0] if len(parts) > 1 else "")
    return letters.upper()


def circle_crop(src, size):
    """Any PIL image -> round RGBA picture of ``size`` pixels."""
    big = ImageOps.fit(src.convert("RGB"), (size * SS, size * SS), Image.LANCZOS)
    mask = Image.new("L", (size * SS, size * SS), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, size * SS - 1, size * SS - 1], fill=255)
    big = big.convert("RGBA")
    big.putalpha(mask)
    return _done(big, size, size)


def initials_avatar(name, seed, size):
    color = _AVATAR_COLORS[int(hashlib.md5(seed.encode("utf-8")).hexdigest(), 16) % len(_AVATAR_COLORS)]
    big = _canvas(size, size)
    d = ImageDraw.Draw(big)
    d.ellipse([0, 0, size * SS - 1, size * SS - 1], fill=color)
    text = initials(name)
    font = _font(int(size * SS * (0.40 if len(text) > 1 else 0.46)))
    d.text((size * SS / 2, size * SS / 2), text, font=font, fill="#ffffff", anchor="mm")
    return _done(big, size, size)


def group_avatar(size, bg="#1f2c42", fg="#f1f5ff"):
    big = _canvas(size, size)
    ImageDraw.Draw(big).ellipse([0, 0, size * SS - 1, size * SS - 1], fill=bg)
    glyph = icons.render("users_fill", int(size * 0.5), fg, stroke=1.8)
    out = _done(big, size, size)
    out.alpha_composite(glyph, ((size - glyph.width) // 2, (size - glyph.height) // 2))
    return out


def add_status_dot(img, color, ring, dot=None):
    """Put a small coloured dot (with a ring in the background colour) at the bottom-right."""
    size = img.width
    dot = dot or max(8, size // 4)
    ringw = max(2, size // 22)
    d = dot + 2 * ringw
    badge = circle_shape(d, fill=ring)
    inner = circle_shape(dot, fill=color)
    badge.alpha_composite(inner, (ringw, ringw))
    out = img.copy()
    pos = (size - d + max(1, size // 40), size - d + max(1, size // 40))
    out.alpha_composite(badge, (max(0, pos[0]), max(0, pos[1])))
    return out


def with_ring(img, color, width=2):
    """Add a coloured ring around a round picture (used by the profile header)."""
    size = img.width
    out = _canvas(size, size)
    d = ImageDraw.Draw(out)
    d.ellipse([0, 0, size * SS - 1, size * SS - 1], outline=color, width=width * SS)
    ring = _done(out, size, size)
    base = img.copy()
    base.alpha_composite(ring)
    return base


# --------------------------------------------------------------------------- #
# toggle switch + wallpapers
# --------------------------------------------------------------------------- #
def toggle(on, w=46, h=26, track_off="#20345b", track_on="#1a73ff", knob="#e8eefc"):
    im = _canvas(w, h)
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, w * SS - 1, h * SS - 1], radius=h * SS // 2, fill=track_on if on else track_off)
    pad = 3
    kd = h - 2 * pad
    x = (w - pad - kd) if on else pad
    d.ellipse([x * SS, pad * SS, (x + kd) * SS, (pad + kd) * SS], fill=knob)
    return _done(im, w, h)


def wallpaper(kind, w, h, base="#030c1c"):
    """Background picture for the chat area, ``w`` x ``h`` pixels."""
    if kind == "plain":
        return Image.new("RGB", (w, h), base)
    if kind == "midnight":
        return Image.new("RGB", (w, h), "#02060f")
    if kind == "ocean":
        im = Image.new("RGB", (w, h))
        d = ImageDraw.Draw(im)
        for y in range(h):
            d.line([(0, y), (w, y)], fill=mix("#06204a", "#030b1c", y / max(1, h - 1)))
        return im
    # "doodle": faint chat icons scattered over the base colour (like the mock-up)
    tile = 300
    t = Image.new("RGBA", (tile, tile), base)
    rnd = random.Random(7)
    names = ["chat", "smile", "phone", "image", "paperclip", "bell", "user", "send", "video", "link"]
    spots = [(x, y) for x in range(20, tile, 100) for y in range(20, tile, 100)]
    for i, (x, y) in enumerate(spots):
        glyph = icons.render(names[i % len(names)], rnd.choice((26, 30, 34)), "#0b1a33", stroke=1.6)
        glyph = glyph.rotate(rnd.choice((-20, -10, 0, 12, 25)), expand=True, resample=Image.BICUBIC)
        t.alpha_composite(glyph, (x + rnd.randint(-10, 20), y + rnd.randint(-10, 20)))
    out = Image.new("RGB", (w, h), base)
    for x in range(0, w, tile):
        for y in range(0, h, tile):
            out.paste(t.convert("RGB"), (x, y))
    return out
