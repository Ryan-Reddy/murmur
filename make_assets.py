"""Generate every image cufflink ships: the icon, the splash, the Store tiles.

    venv\\Scripts\\python.exe make_assets.py

Two marks, not one. The illustrated hero -- the engraved hand coming out of a
cuff, with the wordmark under it -- is the thing people see on the Store page,
and it is lovely at 128 px and up. Below that it is mud: at 16 px the hand
averages to a grey smudge and "cufflink" is an illegible smear. Measured, not
assumed.

So the small sizes get their own mark: two discs and a post, which is a
cufflink seen face-on, and which survives 16 px because it is two circles.
It echoes the cuff button in the hero rather than competing with it.

    <= 71 px   the mark      tray, taskbar, Start list, small tile
    >= 150 px  the hero      large tiles, Store logo, splash

Both are generated here, so a palette change is one command rather than a
morning in an image editor.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ASSETS = Path(__file__).parent / "assets"
HERO = ASSETS / "cufflink-hero.png"

# Sampled from the artwork rather than eyeballed, so the generated pieces and
# the illustration are actually the same colours.
NAVY = (9, 28, 54)          # #091c36  the ground
CREAM = (252, 244, 232)     # #fcf4e8  the hand, the wordmark
MINT = (127, 228, 207)      # #7fe4cf  the waveform
MUTED = (143, 152, 176)

SUPERSAMPLE = 8


def mark(size: int) -> Image.Image:
    """The small mark: a cufflink, face on.

    Drawn at 8x and downsampled -- PIL has no antialiasing, and a 16 px shape
    drawn directly is a judgement about jaggies rather than about the shape.
    """
    n = size * SUPERSAMPLE
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([0, 0, n - 1, n - 1], radius=int(n * 0.22), fill=NAVY)

    radius = n * 0.20
    middle = n * 0.5
    for centre in (n * 0.30, n * 0.70):
        draw.ellipse([centre - radius, middle - radius,
                      centre + radius, middle + radius], fill=CREAM)
    draw.rectangle([n * 0.30, middle - n * 0.075,
                    n * 0.70, middle + n * 0.075], fill=CREAM)
    return img.resize((size, size), Image.LANCZOS)


def hero(size: int) -> Image.Image:
    """The illustration, square, at whatever size is asked for."""
    art = Image.open(HERO).convert("RGB")
    return art.resize((size, size), Image.LANCZOS)


def icon(size: int) -> Image.Image:
    """Whichever mark survives at this size."""
    return mark(size) if size <= 71 else hero(size).convert("RGBA")


def _font(size, bold=True):
    for name in (("segoeuib.ttf", "seguisb.ttf") if bold else ("segoeui.ttf",)):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def wide(width: int, height: int) -> Image.Image:
    """The wide tile: the illustration on the left, the name beside it.

    The hero already carries its own wordmark, so what goes here is the art
    cropped above it -- otherwise the name appears twice at different sizes,
    which reads as a mistake. The crop is to the rounded panel inside the
    artwork, and the tile takes that panel's own colour, so the two do not
    show as a lighter rectangle sitting on a darker one.
    """
    art = Image.open(HERO).convert("RGB")
    # Measured from the file rather than guessed: the panel sits inside a
    # border of flat ground, and the wordmark occupies the bottom third.
    panel = art.crop((int(art.width * 0.097), int(art.height * 0.100),
                      int(art.width * 0.920), int(art.height * 0.901)))
    ground = panel.getpixel((panel.width // 2, 4))
    illustration = panel.crop((0, 0, panel.width, int(panel.height * 0.66)))

    img = Image.new("RGB", (width, height), ground)
    draw = ImageDraw.Draw(img)

    tall = int(height * 0.88)
    scaled = illustration.resize(
        (int(illustration.width * tall / illustration.height), tall), Image.LANCZOS)
    left = int(width * 0.04)
    img.paste(scaled, (left, (height - tall) // 2))

    # Fit the name to whatever room is left, rather than trusting a ratio and
    # letting it run off the edge -- which is exactly what it did.
    text_left = left + scaled.width + int(width * 0.02)
    room = width - text_left - int(width * 0.05)
    points = int(height * 0.30)
    while points > 8:
        face = _font(points)
        box = draw.textbbox((0, 0), "cufflink", font=face)
        if box[2] - box[0] <= room:
            break
        points -= 2
    box = draw.textbbox((0, 0), "cufflink", font=face)
    draw.text((text_left, (height - (box[3] - box[1])) // 2 - box[1]),
              "cufflink", font=face, fill=CREAM)
    return img


def splash() -> Image.Image:
    """Shown the instant the exe starts, while the 310 MB model is read."""
    width, height = 460, 200
    art = Image.open(HERO).convert("RGB")
    panel = art.crop((int(art.width * 0.097), int(art.height * 0.100),
                      int(art.width * 0.920), int(art.height * 0.901)))
    ground = panel.getpixel((panel.width // 2, 4))
    illustration = panel.crop((0, 0, panel.width, int(panel.height * 0.66)))

    img = Image.new("RGB", (width, height), ground)
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, width - 1, height - 1], outline=(32, 56, 92))

    tall = 140
    scaled = illustration.resize(
        (int(illustration.width * tall / illustration.height), tall), Image.LANCZOS)
    img.paste(scaled, (14, (height - tall) // 2))

    left = 14 + scaled.width + 10
    draw.text((left, 68), "cufflink", font=_font(32), fill=CREAM)
    draw.text((left + 2, 112), "warming up the voice…",
              font=_font(13, bold=False), fill=MUTED)
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

# The .ico carries both marks: the shell picks by size, so the tray gets the
# legible one and anything large gets the illustration.
ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]


def store_assets(into: Path) -> int:
    """Every image an MSIX package needs, at every scale."""
    into.mkdir(parents=True, exist_ok=True)
    written = 0
    for name, scales in SQUARE_SCALES.items():
        for pixels, scale in scales:
            icon(pixels).convert("RGB").save(into / f"{name}.scale-{scale}.png")
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
    if not HERO.exists():
        raise SystemExit(f"{HERO} is missing -- the illustration is the source.")
    ASSETS.mkdir(exist_ok=True)

    icon(256).save(ASSETS / "cufflink.ico",
                   sizes=[(s, s) for s in ICO_SIZES],
                   append_images=[icon(s) for s in ICO_SIZES])
    icon(256).convert("RGB").save(ASSETS / "cufflink.png")
    mark(256).save(ASSETS / "cufflink-mark.png")
    splash().save(ASSETS / "splash.png")
    for name in ("cufflink.ico", "cufflink.png", "cufflink-mark.png", "splash.png"):
        print(f"  wrote assets/{name}  ({(ASSETS / name).stat().st_size:,} bytes)")

    count = store_assets(ASSETS / "store")
    print(f"  wrote {count} images to assets/store/ for the MSIX package")
