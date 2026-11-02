"""投稿用の紹介カード画像（墨×朱）"""
from PIL import Image, ImageDraw, ImageFont

SUMI = "#2B2B2B"
SHU = "#C8442C"
KINARI = "#F5F1EA"
USUSHU = "#F6E3DE"
HAI = "#CFC7BB"

FONT_DIR = "/usr/share/fonts/opentype/noto/"


def font(size, weight="Bold"):
    return ImageFont.truetype(f"{FONT_DIR}NotoSansCJK-{weight}.ttc", size, index=0)


def wrap(draw, text, fnt, width):
    lines, line = [], ""
    for ch in text:
        if draw.textlength(line + ch, font=fnt) > width:
            lines.append(line)
            line = ch
        else:
            line += ch
    if line:
        lines.append(line)
    return lines


def fit_name(draw, name, width, max_size, max_lines=2):
    size = max_size
    while size > 40:
        f = font(size, "Black")
        lines = wrap(draw, name, f, width)
        if len(lines) <= max_lines:
            return f, lines
        size -= 6
    return font(40, "Black"), wrap(draw, name, font(40, "Black"), width)[:max_lines]


def stars(rating):
    full = int(rating + 0.25)
    return "★" * full + "☆" * (5 - full)


def draw_card(shop, W, H, path):
    img = Image.new("RGB", (W, H), KINARI)
    d = ImageDraw.Draw(img)
    M = 80
    tall = H > 1500

    # 上部の墨帯
    band = 240 if tall else 190
    top = 160 if tall else 0
    d.rectangle([0, top, W, top + band], fill=SUMI)
    pref = shop["prefecture"]
    pf = font(64)
    pw = d.textlength(pref, font=pf)
    cy = top + band / 2
    d.rectangle([M, cy - 50, M + pw + 60, cy + 50], fill=SHU)
    d.text((M + 30, cy), pref, font=pf, fill=KINARI, anchor="lm")
    sub = "｜".join(x for x in [shop.get("city", ""), shop.get("genre", "")] if x)
    sf = font(40, "Medium")
    sub = (wrap(d, sub, sf, W - M * 2 - pw - 100) or [""])[0]
    d.text((M + pw + 100, cy), sub, font=sf, fill=HAI, anchor="lm")

    y = top + band + (90 if tall else 55)
    # キャッチ
    d.text((M, y), shop.get("catch", ""), font=font(48), fill=SHU, anchor="lt")
    y += 80
    # 店名
    nf, lines = fit_name(d, shop["name"], W - 2 * M, 110 if tall else 92)
    for ln in lines:
        d.text((M, y), ln, font=nf, fill=SUMI, anchor="lt")
        y += int(nf.size * 1.25)
    y += 15 if not tall else 30
    d.line([M, y, W - M, y], fill=HAI, width=4)
    y += 40 if not tall else 50

    # 評価ボックス
    box_h = 210 if tall else 180
    d.rectangle([M, y, W - M, y + box_h], fill=USUSHU)
    d.text((M + 40, y + box_h / 2), f"{shop['rating']:.1f}", font=font(130, "Black"), fill=SHU, anchor="lm")
    sx = M + 340
    d.text((sx, y + 70), stars(shop["rating"]), font=font(56), fill=SHU, anchor="lm")
    d.text((sx, y + 145), f"Googleマップ評価／口コミ{shop['reviews']:,}件", font=font(34, "Medium"), fill=SUMI, anchor="lm")
    y += box_h + (60 if tall else 45)

    # 推しポイント
    pfnt = font(46, "Medium")
    for i, pt in enumerate(shop.get("points", [])[:3], 1):
        d.ellipse([M, y, M + 64, y + 64], fill=SUMI)
        d.text((M + 32, y + 32), str(i), font=font(38), fill=KINARI, anchor="mm")
        for j, ln in enumerate(wrap(d, pt, pfnt, W - 2 * M - 100)[:2]):
            d.text((M + 100, y + 32 + j * 60), ln, font=pfnt, fill=SUMI, anchor="lm")
        lines_n = len(wrap(d, pt, pfnt, W - 2 * M - 100)[:2])
        y += (120 if tall else 95) + (lines_n - 1) * 60

    # フッター
    fy = H - (260 if tall else 60)
    d.line([M, fy - 35, W - M, fy - 35], fill=HAI, width=2)
    d.text((M, fy), "※評価は投稿時点のGoogleマップより", font=font(30, "Regular"), fill="#7A7268", anchor="lm")
    if tall:
        d.text((W / 2, fy + 110), "詳しくはフィード投稿で", font=font(44), fill=SHU, anchor="mm")

    img.save(path, "JPEG", quality=92)


def make_feed_card(shop, path):
    draw_card(shop, 1080, 1350, path)


def make_story_card(shop, path):
    draw_card(shop, 1080, 1920, path)


if __name__ == "__main__":
    sample = {
        "name": "サンプル食堂 東大阪本店", "prefecture": "大阪府", "city": "東大阪市", "genre": "定食・食堂",
        "catch": "週末は行列必至の名物定食", "rating": 4.3, "reviews": 1234,
        "points": ["揚げたてのとんかつが分厚い", "ご飯と味噌汁おかわり自由", "昭和レトロな店内が映える"],
    }
    make_feed_card(sample, "sample_feed.jpg")
    make_story_card(sample, "sample_story.jpg")
