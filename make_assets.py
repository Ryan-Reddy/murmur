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

    draw.text((156, 66), "cufflink", font=font(34, bold=True), fill=FG)
    draw.text((158, 110), "warming up the voice…", font=font(14), fill=MUTED)
    return img


def wide(width: int, height: int) -> Image.Image:
    """The wide and hero tiles: the mark on the ground, off to the left, with
    the name beside it. Centring a disc in a 2.6:1 rectangle looks like a
    mistake; the Store's own tiles are all left-weighted."""
    img = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(img)

    mark_size = int(height * 0.56)
    mark = icon(mark_size)
    left = int(width * 0.10)
    img.paste(mark, (left, (height - mark_size) // 2), mark)

    try:
        face = ImageFont.truetype("segoeuib.ttf", int(height * 0.25))
    except OSError:
        face = ImageFont.load_default()
    text_left = left + mark_size + int(width * 0.045)
    box = draw.textbbox((0, 0), "cufflink", font=face)
    draw.text((text_left, (height - (box[3] - box[1])) // 2 - box[1]),
              "cufflink", font=face, fill=FG)
    return img


# What the Store and the shell actually ask for. Every one of these is
# required by makeappx or shows as a blank tile if missing, and the scale-N
# variants are what stop the icon looking soft on a 150% display -- which is
# most laptops.
SQUARE_SCALES = {
    "Square44x44Logo": [(44, 100), (55, 125), (66, 150), (88, 200), (176, 400)],
    "Square71x71Logo": [(71, 100), (89, 125), (107, 150), (142, 200), (284, 400)],
    "Square150x150Logo": [(150, 100), (188, 125), (225, 150), (300, 200), (600, 400)],
    "Square310x310Logo": [(310, 100), (388, 125), (465, 150), (620, 200)],
    "StoreLogo": [(50, 100), (63, 125), (75, 150), (100, 200), (200, 400)],
}
# Target-size variants sit in the taskbar and Start's list, where Windows wants
# an unplated version -- otherwise it paints a coloured square behind the disc.
TARGET_SIZES = [16, 24, 32, 48, 256]
WIDE_SCALES = [(310, 150, 100), (388, 188, 125), (465, 225, 150), (620, 300, 200)]


def store_assets(into: Path) -> int:
    """Every image an MSIX package needs, at every scale. Returns the count."""
    into.mkdir(parents=True, exist_ok=True)
    written = 0
    for name, scales in SQUARE_SCALES.items():
        for pixels, scale in scales:
            icon(pixels).save(into / f"{name}.scale-{scale}.png")
            written += 1
    for size in TARGET_SIZES:
        # Unplated: transparent ground, so the shell does not draw a tile
        # behind it. Same image, named the way Windows looks for it.
        icon(size).save(into / f"Square44x44Logo.targetsize-{size}.png")
        icon(size).save(
            into / f"Square44x44Logo.targetsize-{size}_altform-unplated.png")
        written += 2
    for width, height, scale in WIDE_SCALES:
        wide(width, height).save(into / f"Wide310x150Logo.scale-{scale}.png")
        written += 1
    wide(2400, 1200).save(into / "SplashScreen.scale-100.png")
    return written + 1


if __name__ == "__main__":
    ASSETS.mkdir(exist_ok=True)
    sizes = [16, 24, 32, 48, 64, 128, 256]
    icon(256).save(ASSETS / "cufflink.ico", sizes=[(s, s) for s in sizes])
    icon(256).save(ASSETS / "cufflink.png")
    splash().save(ASSETS / "splash.png")
    for name in ("cufflink.ico", "cufflink.png", "splash.png"):
        print(f"  wrote assets/{name}  ({(ASSETS / name).stat().st_size:,} bytes)")

    count = store_assets(ASSETS / "store")
    print(f"  wrote {count} images to assets/store/ for the MSIX package")
