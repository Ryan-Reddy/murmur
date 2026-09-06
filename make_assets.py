"""Generate the icon and splash image the packaged build uses.

Run it whenever the look changes; both files are committed so a plain
PyInstaller invocation still works.

    venv\\Scripts\\python.exe make_assets.py
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ASSETS = Path(__file__).parent / "assets"

BG = (30, 27, 46)
PURPLE_TOP = (147, 118, 255)
PURPLE_BOTTOM = (98, 68, 224)
FG = (232, 228, 255)
MUTED = (143, 135, 184)


def _bars(draw, size, colour=(255, 255, 255)):
    """The three sound bars, sized relative to the canvas."""
    unit = size / 64
    for x, height in ((20, 10), (30, 20), (40, 14)):
        draw.rounded_rectangle(
            [x * unit, (32 - height) * unit, (x + 6) * unit, (32 + height) * unit],
            radius=3 * unit, fill=colour,
        )


def icon(size: int) -> Image.Image:
    """The tray mark at poster size: a purple disc with the sound bars on it.

    Drawn at 4x and downsampled, because PIL has no antialiasing of its own and
    a 32 px icon with a jagged circle looks like a screenshot of an icon.
    """
    scale = 4
    big = size * scale
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))

    # A vertical gradient reads as depth at large sizes and averages out to the
    # flat purple of the tray icon at small ones.
    gradient = Image.new("RGB", (1, big))
    for y in range(big):
        t = y / max(big - 1, 1)
        gradient.putpixel((0, y), tuple(
            round(a + (b - a) * t) for a, b in zip(PURPLE_TOP, PURPLE_BOTTOM)
        ))
    gradient = gradient.resize((big, big))

    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).ellipse([scale * 2, scale * 2, big - scale * 2, big - scale * 2], fill=255)
    img.paste(gradient, (0, 0), mask)

    _bars(ImageDraw.Draw(img), big)
    return img.resize((size, size), Image.LANCZOS)


def splash() -> Image.Image:
    """Shown the instant the exe starts, while the 310 MB model is read."""
    width, height = 460, 200
    img = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, width - 1, height - 1], outline=(58, 53, 80))

    mark = icon(84)
    img.paste(mark, (44, 58), mark)

    def font(size, bold=False):
        for name in (("segoeuib.ttf", "seguisb.ttf") if bold else ("segoeui.ttf",)):
            try:
                return ImageFont.truetype(name, size)
            except OSError:
                continue
        return ImageFont.load_default()

    draw.text((156, 66), "Murmur", font=font(34, bold=True), fill=FG)
    draw.text((158, 110), "warming up the voice…", font=font(14), fill=MUTED)
    return img


if __name__ == "__main__":
    ASSETS.mkdir(exist_ok=True)
    sizes = [16, 24, 32, 48, 64, 128, 256]
    icon(256).save(ASSETS / "murmur.ico", sizes=[(s, s) for s in sizes])
    icon(256).save(ASSETS / "murmur.png")
    splash().save(ASSETS / "splash.png")
    for name in ("murmur.ico", "murmur.png", "splash.png"):
        print(f"  wrote assets/{name}  ({(ASSETS / name).stat().st_size:,} bytes)")
