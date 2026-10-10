"""Display-free tests: icons, profile-picture preparation, shapes and text wrapping."""
import base64
import io

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from client import icons, images  # noqa: E402
from shared import protocol as P  # noqa: E402


@pytest.mark.parametrize("name", sorted(icons.ICONS))
def test_every_icon_renders_with_visible_pixels(name):
    img = icons.render(name, 32, "#ffffff")
    assert img.size == (32, 32) and img.getchannel("A").getextrema()[1] > 200


def test_icon_path_parser_handles_arcs_relative_commands_and_closing():
    subs = icons.parse("M2 2 h4 v4 a2 2 0 0 1 -2 2 l-2 0 z M10 10 L12 12")
    assert len(subs) == 2 and subs[0][1] is True and subs[1][1] is False
    assert subs[0][0][0] == (2, 2) and subs[0][0][1] == (6, 2)


def _save(tmp_path, name, size, mode="RGB", fmt=None, color=(10, 120, 200)):
    path = tmp_path / name
    Image.new(mode, size, color).save(path, fmt)
    return str(path)


def test_prepare_avatar_makes_a_small_square_jpeg_the_server_accepts(tmp_path):
    for src in (_save(tmp_path, "wide.png", (1600, 900)),
                _save(tmp_path, "tall.jpg", (500, 2000)),
                _save(tmp_path, "alpha.png", (300, 300), "RGBA", color=(255, 0, 0, 0)),
                _save(tmp_path, "tiny.gif", (20, 20), "P", color=3)):
        data = images.prepare_avatar(src)
        assert P.validate_avatar(data) is None and len(data) <= P.MAX_AVATAR_CHARS
        out = Image.open(io.BytesIO(base64.b64decode(data)))
        assert out.format == "JPEG" and out.size == (images.AVATAR_PX, images.AVATAR_PX)


def test_prepare_avatar_noisy_photo_still_fits(tmp_path):
    import os
    noise = Image.frombytes("RGB", (900, 900), os.urandom(900 * 900 * 3))
    path = tmp_path / "noise.png"
    noise.save(path)
    assert len(images.prepare_avatar(str(path))) <= P.MAX_AVATAR_CHARS


def test_prepare_avatar_rejects_non_images_and_missing_files(tmp_path):
    txt = tmp_path / "a.txt"
    txt.write_text("hello")
    with pytest.raises(images.AvatarError):
        images.prepare_avatar(str(txt))
    with pytest.raises(images.AvatarError):
        images.prepare_avatar(str(tmp_path / "missing.png"))


def test_decode_avatar_roundtrip_and_damaged_data(tmp_path):
    data = images.prepare_avatar(_save(tmp_path, "p.png", (100, 100)))
    assert images.decode_avatar(data).size == (256, 256)
    assert images.decode_avatar("////") is None


def test_initials_and_shapes():
    assert images.initials("David Williams") == "DW" and images.initials("tolu") == "T"
    assert images.initials("") == "?" and images.initials("ada_lovelace") == "AL"
    assert images.initials_avatar("David Williams", "david", 48).size == (48, 48)
    assert images.group_avatar(50).size == (50, 50)
    assert images.circle_crop(Image.new("RGB", (80, 40), "red"), 40).size == (40, 40)
    assert images.bubble(100, 40, "#0a6cff", tail=True).size == (109, 40)
    assert images.gradient_rrect(120, 40, 12, "#0f65fe", "#062761").size == (120, 40)
    assert images.wallpaper("doodle", 400, 300).size == (400, 300)
    dot = images.add_status_dot(images.initials_avatar("A B", "ab", 60), "#22c55e", "#000000")
    assert dot.size == (60, 60)


def test_mix_colours():
    assert images.mix("#000000", "#ffffff", 0.5) == "#808080"


def test_wrap_text_splits_words_and_long_words():
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    try:
        from tkinter import font as tkfont
        from client.widgets import wrap_text
        f = tkfont.Font(root, family="TkDefaultFont", size=-14)
        lines = wrap_text(f, "the quick brown fox jumps over the lazy dog", 120)
        assert len(lines) > 2 and all(f.measure(line) <= 120 for line in lines)
        assert " ".join(lines) == "the quick brown fox jumps over the lazy dog"
        long = wrap_text(f, "x" * 200, 100)
        assert len(long) > 1 and all(f.measure(line) <= 100 for line in long) and "".join(long) == "x" * 200
        assert wrap_text(f, "a\n\nb", 100) == ["a", "", "b"]
    finally:
        root.destroy()
