"""Make collection posters in the style of the ones already used for Trending, Popular, etc.

Those come from ThePosterDB: a dark gradient, an orange-to-yellow frame, the Plex chevron, and the name in
orange-gradient Open Sans Bold capitals ("New Episodes" adds a thin rule between two lines). This takes the
Trending poster as the base, paints over its word with the background (rebuilt from the pixels around it),
and sets new text in the same font, size, position and colours. The poster itself isn't stored in this repo:
it's downloaded when the script runs.

usage: python3 make_collection_posters.py OUT_DIR [--check] [--brand]
  --check   also writes check_trending.png: "TRENDING" redrawn next to the original, to compare
  --brand   also makes branded streaming posters (service logo and colours) in OUT_DIR/brand
Needs Pillow, and Open Sans (fonts/OpenSans.ttf next to this script, downloaded if missing; SIL OFL).
"""
import os
import random
import sys
import urllib.request

from PIL import Image, ImageChops, ImageDraw, ImageFont

BASE_URL = "https://theposterdb.com/api/assets/345270"   # Trending
FONT_URL = "https://github.com/google/fonts/raw/main/ofl/opensans/OpenSans%5Bwdth%2Cwght%5D.ttf"
HERE = os.path.dirname(os.path.abspath(__file__))

# Measured from the originals (1000x1500): capitals are ~94 px tall; one line sits at y 1037-1131,
# two lines at 977-1071 and 1107-1201; the rule (New Episodes) is 8 px at y 1100, 450 px wide, and pushes
# the second line down to 1137. Text runs orange (left) to yellow (right).
CAP = 94
SIZE = 128                      # Open Sans Bold at 128 px matches the originals' capitals (94 px, same width)
ONE_LINE_TOP = 1037
TWO_LINE_TOPS = (977, 1107)
RULE_TOPS = (979, 1137)
RULE_Y, RULE_H, RULE_W = 1100, 8, 450
MAX_W = 760                     # widest line in the originals is ~660 px; the frame's inside is ~930
LEFT, RIGHT = (232, 146, 49), (239, 184, 31)
PATCH = (155, 960, 845, 1252)   # the area the words can occupy, repainted with background

# Posters to make: file name -> lines (a "|" line is the rule)
POSTERS = {
    # streaming rows
    "Top on Netflix": ["TOP ON", "|", "NETFLIX"],
    "Top on Prime Video": ["TOP ON", "|", "PRIME VIDEO"],
    "Top on Disney+": ["TOP ON", "|", "DISNEY+"],
    "Top on Apple TV": ["TOP ON", "|", "APPLE TV"],
    "Top on Paramount+": ["TOP ON", "|", "PARAMOUNT+"],
    "Top on MUBI": ["TOP ON", "|", "MUBI"],
    "Top on BBC iPlayer": ["TOP ON", "|", "BBC IPLAYER"],
    "Top on ITVX": ["TOP ON", "|", "ITVX"],
    "Top on Channel 4": ["TOP ON", "|", "CHANNEL 4"],
    "Top on NOW": ["TOP ON", "|", "NOW"],
    # score rows
    "Critically Acclaimed": ["CRITICALLY", "ACCLAIMED"],
    "Hidden Gems": ["HIDDEN", "GEMS"],
    "Under 100 Minutes": ["UNDER 100", "MINUTES"],
    "Best Documentaries": ["BEST", "DOCUMENTARIES"],
    "Binge-able": ["BINGE-ABLE"],
    # kids age rows
    "All Ages (U)": ["ALL AGES", "(U)"],
    "Parental Guidance (PG)": ["PARENTAL", "GUIDANCE (PG)"],
}


def fetch(url, path):
    if not os.path.exists(path):
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with open(path, "wb") as f:
            f.write(urllib.request.urlopen(req, timeout=60).read())
    return path


def blank(base):
    """Paint over the word: fill PATCH by blending its four edges (Coons patch), plus the faint grain."""
    im = base.copy()
    px = im.load()
    x0, y0, x1, y1 = PATCH

    def avg(points):
        pts = [px[x, y] for x, y in points]
        return tuple(sum(p[c] for p in pts) / len(pts) for c in range(3))

    top = [avg([(x, y0 - d) for d in range(1, 6)]) for x in range(x0, x1 + 1)]
    bot = [avg([(x, y1 + d) for d in range(1, 6)]) for x in range(x0, x1 + 1)]
    lef = [avg([(x0 - d, y) for d in range(1, 6)]) for y in range(y0, y1 + 1)]
    rig = [avg([(x1 + d, y) for d in range(1, 6)]) for y in range(y0, y1 + 1)]
    c00, c10, c01, c11 = top[0], top[-1], bot[0], bot[-1]
    rnd = random.Random(1)
    for j, y in enumerate(range(y0, y1 + 1)):
        v = j / (y1 - y0)
        for i, x in enumerate(range(x0, x1 + 1)):
            u = i / (x1 - x0)
            p = []
            for c in range(3):
                val = ((1 - v) * top[i][c] + v * bot[i][c] + (1 - u) * lef[j][c] + u * rig[j][c]
                       - ((1 - u) * (1 - v) * c00[c] + u * (1 - v) * c10[c] + (1 - u) * v * c01[c] + u * v * c11[c]))
                p.append(max(0, min(255, round(val + rnd.uniform(-0.6, 0.6)))))
            px[x, y] = tuple(p)
    return im


def gradient_fill(im, mask, left=LEFT, right=RIGHT):
    """Colour the mask with a left-to-right colour run (orange to yellow, as on the originals, by default)."""
    w, h = im.size
    grad = Image.new("RGB", (w, 1))
    gp = grad.load()
    for x in range(w):
        t = min(1, max(0, (x - 170) / (830 - 170)))
        gp[x, 0] = tuple(round(left[c] + (right[c] - left[c]) * t) for c in range(3))
    im.paste(grad.resize((w, h)), (0, 0), mask)


def font_for(path, size, width=100):
    f = ImageFont.truetype(path, size)
    f.set_variation_by_axes([700, width])      # Bold; width 100 = normal, down to 75 = condensed
    return f


def render(blank_base, lines, font_path, colours=(LEFT, RIGHT)):
    im = blank_base.copy()
    w = im.size[0]
    mask = Image.new("L", im.size, 0)
    d = ImageDraw.Draw(mask)
    words = [ln for ln in lines if ln != "|"]
    ruled = "|" in lines
    tops = [ONE_LINE_TOP] if len(words) == 1 else (RULE_TOPS if ruled else TWO_LINE_TOPS)
    size0 = SIZE
    for word, top in zip(words, tops):
        # A long word is first narrowed (Open Sans's condensed widths, same height), and only made smaller
        # if it still doesn't fit, so the lines keep the same size as the originals where possible.
        size, width = size0, 100
        f = font_for(font_path, size, width)
        while d.textlength(word, font=f) > MAX_W and width > 75:
            width -= 1
            f = font_for(font_path, size, width)
        while d.textlength(word, font=f) > MAX_W:
            size -= 1
            f = font_for(font_path, size, width)
        cap = CAP * size / size0
        bbox = d.textbbox((0, 0), "H", font=f)      # place by cap height, so every line sits the same way
        y = top + (CAP - cap) / 2 - bbox[1]
        d.text(((w - d.textlength(word, font=f)) / 2, y), word, font=f, fill=255)
    if ruled:
        d.rectangle([(w - RULE_W) / 2, RULE_Y, (w + RULE_W) / 2 - 1, RULE_Y + RULE_H - 1], fill=255)
    gradient_fill(im, mask, *colours)
    return im


# ---- Branded versions of the streaming posters: the service's logo in place of the Plex chevron, and its colours
# for the frame and words. Logos come from Wikipedia / Wikimedia Commons when the script runs (not stored here).
# hex colours: background light/dark (diagonal, like the originals), text/frame dark->light, logo "white" or "as is"
WIKI_FILE = "https://{wiki}.wikipedia.org/wiki/Special:FilePath/{file}?width=1600"
BRANDS = {
    "Top on Netflix":     ("commons", "Netflix_2015_logo.svg",            "#1c1c1c", "#0a0a0a", "#b20710", "#e50914", "as is"),
    "Top on Prime Video": ("commons", "Prime_Video_logo_(2024).svg",      "#1b2633", "#0b1219", "#1a98ff", "#5cb6ff", "as is"),
    "Top on Disney+":     ("commons", "Disney%2B_2024.svg",               "#14275a", "#040a1e", "#1f7fe0", "#58d6e8", "white"),
    "Top on Apple TV":    ("en",      "Apple_TV_(logo).svg",              "#222224", "#000000", "#a1a1a6", "#ffffff", "white"),
    "Top on Paramount+":  ("commons", "Paramount_Plus.svg",               "#0c2457", "#020a1f", "#0064ff", "#4a90ff", "white"),
    "Top on MUBI":        ("commons", "Mubi_logo.svg",                    "#202020", "#050505", "#b4b4b4", "#ffffff", "white"),
    "Top on BBC iPlayer": ("commons", "BBC_iPlayer_(2021).svg",           "#2a1322", "#0b0509", "#d1176e", "#ff4c98", "as is"),
    "Top on ITVX":        ("en",      "ITVX_logo.svg",                    "#1f1f22", "#08080a", "#bfd02a", "#e4f35a", "as is"),
    "Top on Channel 4":   ("en",      "Channel_4_(On_Demand)_2023.svg",   "#1d211d", "#070907", "#86d95a", "#b7fa8a", "as is"),
    "Top on NOW":         ("commons", "Now_logo.svg",                     "#0d2b29", "#03100f", "#0e9f8f", "#3ddcc6", "white"),
}
BRAND_WORDS = ["MOST", "WATCHED"]
LOGO_CENTRE = (500, 506)       # where the chevron sits on the originals (x 380-665, y 304-709)
LOGO_AREA, LOGO_MAX_W, LOGO_MAX_H = 150000, 680, 360      # similar visual weight for wide and tall logos


def hexrgb(h):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def brand_base(light, dark, frame_dark, frame_light):
    """Dark diagonal gradient with the faint grain of the originals, and a frame running light (top) to dark."""
    small = Image.new("RGB", (40, 60))
    sp = small.load()
    for y in range(60):
        for x in range(40):
            t = min(1, max(0, 0.45 * x / 39 + 0.55 * y / 59 + 0.15 * (((x - 12) / 40) ** 2 + ((y - 18) / 60) ** 2)))
            sp[x, y] = tuple(round(light[c] + (dark[c] - light[c]) * t) for c in range(3))
    im = small.resize((1000, 1500), Image.BICUBIC)
    noise = Image.effect_noise((1000, 1500), 6).convert("RGB")
    im = Image.blend(im, ImageChops.add(im, noise, scale=1, offset=-128), 0.15)
    frame = Image.new("L", im.size, 0)
    ImageDraw.Draw(frame).rounded_rectangle([15, 15, 984, 1484], radius=10, outline=255, width=20)
    grad = Image.new("RGB", (1, 1500))
    gp = grad.load()
    for y in range(1500):
        t = y / 1499
        gp[0, y] = tuple(round(frame_light[c] + (frame_dark[c] - frame_light[c]) * t) for c in range(3))
    im.paste(grad.resize(im.size), (0, 0), frame)
    return im


def place_logo(im, logo, treatment):
    logo = logo.convert("RGBA")
    logo = logo.crop(logo.getchannel("A").getbbox())
    if treatment == "white":
        white = Image.new("RGBA", logo.size, (255, 255, 255, 255))
        white.putalpha(logo.getchannel("A"))
        logo = white
    w, h = logo.size
    k = min((LOGO_AREA / (w * h)) ** 0.5, LOGO_MAX_W / w, LOGO_MAX_H / h)
    logo = logo.resize((round(w * k), round(h * k)), Image.LANCZOS)
    im.paste(logo, (LOGO_CENTRE[0] - logo.width // 2, LOGO_CENTRE[1] - logo.height // 2), logo)
    return im


def make_brand_posters(out, font_path):
    logo_dir = os.path.join(out, ".logos")
    os.makedirs(logo_dir, exist_ok=True)
    for name, (wiki, file, light, dark, c1, c2, treatment) in BRANDS.items():
        logo_path = os.path.join(logo_dir, file.replace("%2B", "+").rsplit(".", 1)[0] + ".png")
        fetch(WIKI_FILE.format(wiki=wiki, file=file), logo_path)
        c1, c2 = hexrgb(c1), hexrgb(c2)
        im = brand_base(hexrgb(light), hexrgb(dark), c1, c2)
        im = place_logo(im, Image.open(logo_path), treatment)
        im = render(im, BRAND_WORDS, font_path, colours=(c1, c2))
        im.save(os.path.join(out, f"{name}.jpg"), quality=92, subsampling=0)
        print("made (brand)", name)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 1:
        sys.exit(__doc__)
    out = args[0]
    os.makedirs(out, exist_ok=True)
    os.makedirs(os.path.join(HERE, "fonts"), exist_ok=True)
    font_path = fetch(FONT_URL, os.path.join(HERE, "fonts", "OpenSans.ttf"))
    base = Image.open(fetch(BASE_URL, os.path.join(out, ".base_trending.png"))).convert("RGB")
    clean = blank(base)
    if "--check" in sys.argv:
        redo = render(clean, ["TRENDING"], font_path)
        diff = ImageChops.difference(base, redo).convert("L").point(lambda v: 255 if v > 24 else 0)
        side = Image.new("RGB", (3000, 1500))
        side.paste(base, (0, 0)); side.paste(redo, (1000, 0)); side.paste(diff.convert("RGB"), (2000, 0))
        side.save(os.path.join(out, "check_trending.png"))
        print("pixels differing by >24:", sum(1 for v in diff.getdata() if v))
    if "--brand" in sys.argv:          # branded streaming posters, into OUT_DIR/brand
        make_brand_posters(os.path.join(out, "brand"), font_path)
    for name, lines in POSTERS.items():
        # JPEG at 92 with full colour resolution: ~100-150 KB a poster, against ~1 MB as PNG (Plex keeps every upload)
        render(clean, lines, font_path).save(os.path.join(out, f"{name}.jpg"), quality=92, subsampling=0)
        print("made", name)


if __name__ == "__main__":
    main()
