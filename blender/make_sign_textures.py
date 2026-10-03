"""
Generates the placeholder sign / screen texture atlases used by build_hex_city.py.
Generic Japanese shop words only (ramen, karaoke, pharmacy ...) - no real brands or logos.

    python make_sign_textures.py          (needs Pillow + a Japanese font, e.g. IPAGothic / Noto CJK)

Atlases (2048 x 2048):
    textures/signs_h.png   4 x 16 cells of 512x128  horizontal signs / tenant panels / building names
    textures/signs_v.png  16 x 4  cells of 128x512  tategaki signs / stacked tenant towers / directories
    textures/screens.png   4 x 4  cells of 512x512  digital-ad placeholders
"""
import os
import random
from PIL import Image, ImageDraw, ImageFont, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "textures")
JP_FONTS = ["/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf",
            "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
            "C:/Windows/Fonts/meiryo.ttc", "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc"]
JP = next((f for f in JP_FONTS if os.path.exists(f)), None)

R = random.Random(7)

SHOPS = ["ラーメン", "カラオケ", "居酒屋", "焼肉", "寿司", "カフェ", "薬局", "書店", "古着", "ゲーム",
         "美容室", "歯科", "英会話", "不動産", "喫茶", "うどん", "餃子", "ネイル", "スタジオ", "整体",
         "ホテル", "駐車場", "そば", "天ぷら", "眼鏡", "中古CD", "パン", "花屋", "時計", "写真",
         "ダンス", "漫画喫茶", "牛丼", "定食", "焼き鳥", "ボウリング", "ビリヤード", "雑貨", "ジム", "コーヒー",
         "クリニック", "スニーカー", "麻雀", "占い"]
ROMAJI = {"ラーメン": "RAMEN", "カラオケ": "KARAOKE", "寿司": "SUSHI", "カフェ": "CAFE", "ゲーム": "GAME",
          "古着": "VINTAGE", "焼肉": "BBQ", "居酒屋": "IZAKAYA", "喫茶": "KISSA", "ホテル": "HOTEL",
          "ジム": "FITNESS", "コーヒー": "COFFEE", "ダンス": "DANCE STUDIO", "スニーカー": "SNEAKERS"}
SYMBOLS = ["麺", "酒", "薬", "寿", "肉", "本", "茶", "湯", "歌", "服"]
NAMES = ["SAKURA BLDG", "第一ビル", "AOI BUILDING", "ミドリビル", "HOSHI 3", "SORA TERRACE", "第二共栄ビル",
         "KAZE BLDG", "NAMI 21", "ひかりビル", "TSUKI PLAZA", "HANA BLDG"]
MISC = ["24H", "OPEN", "営業中", "GAME CENTER", "KARAOKE 24H", "¥100", "MART 24", "B1F BAR"]

# (background, text, accent)
SCHEMES = [("#f4f1e8", "#c8102e", "#c8102e"), ("#c8102e", "#ffffff", "#ffd400"), ("#ffd400", "#111111", "#c8102e"),
           ("#14213d", "#ffffff", "#ff7a1a"), ("#111111", "#ffe14d", "#ff2fa8"), ("#0b6e4f", "#ffffff", "#f2c230"),
           ("#ffffff", "#14213d", "#2a6fdb"), ("#ff7a1a", "#ffffff", "#14213d"), ("#2a6fdb", "#ffffff", "#ffffff"),
           ("#111111", "#2de2ff", "#2de2ff"), ("#7b2cff", "#ffffff", "#ffe14d"), ("#e8e2d0", "#3a2a1a", "#8b1e1e"),
           ("#1f1f1f", "#ffffff", "#c8102e"), ("#ff2fa8", "#ffffff", "#111111")]


def font(size, latin=False):
    if latin:
        for f in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", JP):
            if f and os.path.exists(f):
                return ImageFont.truetype(f, size)
    return ImageFont.truetype(JP, size) if JP else ImageFont.load_default()


def is_latin(s):
    return all(ord(c) < 0x3000 for c in s)


def fit_text(draw, text, box_w, box_h, start, latin=False, stroke=0):
    size = start
    while size > 10:
        f = font(size, latin)
        l, t, r, b = draw.textbbox((0, 0), text, font=f, stroke_width=stroke)
        if r - l <= box_w and b - t <= box_h:
            return f, (r - l, b - t, l, t)
        size -= 3
    f = font(size, latin)
    l, t, r, b = draw.textbbox((0, 0), text, font=f)
    return f, (r - l, b - t, l, t)


def centered(draw, text, cx, cy, box_w, box_h, start, fill, stroke=0, stroke_fill=None):
    f, (w, h, l, t) = fit_text(draw, text, box_w, box_h, start, is_latin(text), stroke)
    draw.text((cx - w / 2 - l, cy - h / 2 - t), text, font=f, fill=fill, stroke_width=stroke,
              stroke_fill=stroke_fill or fill)


def h_sign(text, sub=None, scheme=None, style=0):
    bg, fg, ac = scheme or R.choice(SCHEMES)
    im = Image.new("RGB", (512, 128), bg)
    d = ImageDraw.Draw(im)
    if style == 0:
        d.rectangle((6, 6, 505, 121), outline=ac, width=5)
        centered(d, text, 256, 58 if sub else 64, 460, 82 if not sub else 70, 96, fg)
        if sub:
            centered(d, sub, 256, 104, 300, 20, 22, ac)
    elif style == 1:
        d.rectangle((0, 0, 128, 127), fill=ac)
        centered(d, R.choice(SYMBOLS), 64, 64, 104, 104, 100, bg)
        centered(d, text, 320, 64, 360, 90, 90, fg)
    elif style == 2:
        d.rectangle((0, 92, 511, 127), fill=ac)
        centered(d, text, 256, 48, 470, 76, 90, fg)
        if sub:
            centered(d, sub, 256, 110, 300, 24, 24, bg)
    else:
        d.rectangle((0, 0, 511, 18), fill=ac)
        d.rectangle((0, 110, 511, 127), fill=ac)
        centered(d, text, 256, 64, 470, 80, 92, fg)
    return im


def v_sign(text, scheme=None):
    bg, fg, ac = scheme or R.choice(SCHEMES)
    im = Image.new("RGB", (128, 512), bg)
    d = ImageDraw.Draw(im)
    d.rectangle((4, 4, 123, 507), outline=ac, width=4)
    chars = list(text)[:5]
    n = len(chars)
    cell = min(96, 470 // n)
    y0 = 256 - cell * n / 2
    for i, c in enumerate(chars):
        centered(d, c, 64, y0 + cell * (i + 0.5), 104, cell - 6, 100, fg)
    return im


def v_stack():
    im = Image.new("RGB", (128, 512), "#222222")
    d = ImageDraw.Draw(im)
    n = R.choice([4, 5, 6])
    h = 512 / n
    for i in range(n):
        bg, fg, ac = R.choice(SCHEMES)
        d.rectangle((3, i * h + 3, 124, (i + 1) * h - 3), fill=bg)
        word = R.choice(SHOPS)
        floor = "%dF" % (n - i + 1)
        centered(d, floor, 64, i * h + 16, 60, 18, 20, ac)
        centered(d, word[:3], 64, i * h + h / 2 + 8, 112, h - 40, 60, fg)
    return im


def v_directory():
    im = Image.new("RGB", (128, 512), "#1b1d21")
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, 127, 44), fill="#d8d4c8")
    centered(d, "案内", 64, 22, 100, 34, 34, "#1b1d21")
    floors = R.randint(6, 9)
    for i in range(floors):
        y = 56 + i * 50
        d.rectangle((6, y, 121, y + 42), fill="#2b2e33")
        centered(d, "%dF" % (floors - i), 24, y + 21, 32, 22, 22, "#ffd400")
        centered(d, R.choice(SHOPS)[:4], 80, y + 21, 78, 30, 30, "#ffffff")
    return im


def screen_ad(i):
    rr = random.Random(100 + i)
    pal = [("#ff2d95", "#7b2cff"), ("#ff7a00", "#ffd23f"), ("#00e5ff", "#0047ff"), ("#ff3b3b", "#151515"),
           ("#8a2be2", "#ff4fd8"), ("#00ffa3", "#00a3ff"), ("#ff6b00", "#1b2a4a"), ("#ffd1dc", "#5a2bff")][i % 8]
    im = Image.new("RGB", (512, 512))
    d = ImageDraw.Draw(im)
    c0 = tuple(int(pal[0][k:k + 2], 16) for k in (1, 3, 5))
    c1 = tuple(int(pal[1][k:k + 2], 16) for k in (1, 3, 5))
    for y in range(512):
        t = y / 511
        d.line([(0, y), (511, y)], fill=tuple(int(c0[k] * (1 - t) + c1[k] * t) for k in range(3)))
    for _ in range(rr.randint(2, 4)):
        x, y, r = rr.randint(0, 512), rr.randint(0, 512), rr.randint(60, 220)
        col = rr.choice([(255, 255, 255), c0, c1, (255, 230, 80)])
        d.ellipse((x - r, y - r, x + r, y + r), outline=col, width=rr.randint(6, 26))
    for _ in range(rr.randint(1, 3)):
        y = rr.randint(40, 470)
        d.polygon([(0, y), (512, y - rr.randint(-160, 160)), (512, y + 40), (0, y + 40)],
                  fill=(255, 255, 255) if rr.random() < 0.5 else (20, 20, 30))
    im = im.filter(ImageFilter.GaussianBlur(1.2))
    d = ImageDraw.Draw(im)
    word = ["SALE", "NEW", "LIVE", "MUSIC", "新発売", "限定", "夏", "セール", "ライブ", "STREET", "ゲーム",
            "SNEAKERS", "NIGHT", "東京", "映画", "PLAY"][i % 16]
    centered(d, word, 256, 250, 460, 220, 260, (255, 255, 255), stroke=6, stroke_fill=(10, 10, 20))
    sub = ["COMING SOON", "NOW ON", "2F-5F", "TONIGHT 19:00", "期間限定", "OPEN 10-23"][i % 6]
    centered(d, sub, 256, 440, 380, 44, 46, (255, 255, 255))
    return im


def build():
    os.makedirs(OUT, exist_ok=True)
    # horizontal atlas
    H = Image.new("RGB", (2048, 2048), "#000000")
    cells = []
    for i, w in enumerate(SHOPS):
        cells.append(h_sign(w, ROMAJI.get(w), style=i % 4))
    for n in NAMES:
        cells.append(h_sign(n, None, scheme=R.choice([("#d8d4c8", "#2b2b2b", "#2b2b2b"),
                                                     ("#2b2b2b", "#f2f2f2", "#9a9a9a"),
                                                     ("#c9b38a", "#1d1d1d", "#1d1d1d")]), style=0))
    for m in MISC:
        cells.append(h_sign(m, None, style=3))
    for k, im in enumerate(cells[:64]):
        H.paste(im, ((k % 4) * 512, (k // 4) * 128))
    H.save(os.path.join(OUT, "signs_h.png"))
    # vertical atlas
    V = Image.new("RGB", (2048, 2048), "#000000")
    vcells = [v_sign(w) for w in (SHOPS * 2)[:32]]
    vcells += [v_stack() for _ in range(16)]
    vcells += [v_directory() for _ in range(8)]
    vcells += [v_sign(w, scheme=("#111111", c, c)) for w, c in zip(["カラオケ", "ゲーム", "ホテル", "居酒屋", "ラーメン",
                                                                     "ライブ", "東京", "ダンス"],
                                                                    ["#2de2ff", "#ff2fa8", "#ffd400", "#ff7a1a",
                                                                     "#ffffff", "#8a4dff", "#2de2ff", "#ff2fa8"])]
    for k, im in enumerate(vcells[:64]):
        V.paste(im, ((k % 16) * 128, (k // 16) * 512))
    V.save(os.path.join(OUT, "signs_v.png"))
    # screens
    S = Image.new("RGB", (2048, 2048))
    for k in range(16):
        S.paste(screen_ad(k), ((k % 4) * 512, (k // 4) * 512))
    S.save(os.path.join(OUT, "screens.png"))
    print("textures written to", OUT, "font:", JP)


if __name__ == "__main__":
    build()
