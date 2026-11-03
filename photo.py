"""
自分で撮った写真から投稿下書きを作る
入力: posts/<post_id>/raw_*.jpg（LINEから届いた写真）、MEMO（店名・エリア・ひとこと）
出力: photo_1.jpg… + feed.jpg（まとめカード）+ story.jpg + post.json
"""
import base64
import io
import json
import os
import sys
from pathlib import Path

import anthropic
import requests
from PIL import Image, ImageDraw, ImageOps

from card import SHU, SUMI, WHITE, font, make_feed_card, stars, wrap
from main import HISTORY, PREFECTURES, extract_json, final_text, load_history

MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")
post_id = os.environ["POST_ID"]
memo = os.environ.get("MEMO", "").strip()
out = Path(__file__).parent / "posts" / post_id
client = anthropic.Anthropic()


def fit(img, w, h):
    return ImageOps.fit(img, (w, h), Image.LANCZOS, centering=(0.5, 0.5))


def load_raw():
    files = sorted(out.glob("raw_*.jpg"), key=lambda p: int(p.stem.split("_")[1]))
    if not files:
        sys.exit("写真が見つからへん")
    return [ImageOps.exif_transpose(Image.open(f)).convert("RGB") for f in files[:9]]


def lookup_place(query):
    r = requests.post(
        "https://places.googleapis.com/v1/places:searchText",
        headers={
            "X-Goog-Api-Key": os.environ["GOOGLE_PLACES_API_KEY"],
            "X-Goog-FieldMask": "places.id,places.displayName,places.formattedAddress,places.rating,"
            "places.userRatingCount,places.googleMapsUri,places.primaryTypeDisplayName",
        },
        json={"textQuery": query, "languageCode": "ja", "regionCode": "JP"},
        timeout=30,
    )
    r.raise_for_status()
    places = r.json().get("places", [])
    return places[0] if places else None


def b64(img):
    small = img.copy()
    small.thumbnail((1024, 1024))
    buf = io.BytesIO()
    small.save(buf, "JPEG", quality=80)
    return base64.b64encode(buf.getvalue()).decode()


def write_caption(shop, photos):
    prompt = f"""Instagramのグルメ紹介アカウント「全国バズグルメ」の投稿文を作ってください。
今回はアカウント運営者が実際にお店に行って撮った写真です。

店名: {shop['name']}
住所: {shop.get('address', '不明')}
ジャンル: {shop.get('genre', '不明')}
Googleマップ評価: {shop.get('rating') or 'なし'}（口コミ{shop.get('reviews') or 0}件）
運営者のメモ: {memo or 'なし'}

ルール:
- 写真に写っている料理・お店の雰囲気を具体的に描写し、実際に行った人の目線で書く
- 味の感想は運営者のメモにあるものだけ。書かれていない味の評価を作らない
- 「体にいい」「〇〇に効く」等の健康効果、根拠のない最上級表現（日本一など）は書かない
- 価格や営業時間など、メモや写真で確認できない情報は書かない
- 親しみやすい口調、絵文字は控えめ。1行目は思わず読みたくなるフックにする
- 評価がある場合は本文の最後に「⭐Googleマップ評価 X（N件・投稿時点）」を入れる
- 最後に「※最新の営業情報はお店の公式情報をご確認ください」
- ハッシュタグは10〜15個。都道府県が分かれば #〇〇グルメ を含める

次のJSONだけで返してください:
```json
{{"catch": "画像に載せる短いキャッチ（18字以内）", "points": ["推しポイント1（16字以内）", "推しポイント2", "推しポイント3"], "genre": "ジャンル（10字以内）", "caption": "投稿本文（ハッシュタグ含む）"}}
```"""
    content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64(p)}} for p in photos[:4]]
    content.append({"type": "text", "text": prompt})
    resp = client.messages.create(model=MODEL, max_tokens=2000, messages=[{"role": "user", "content": content}])
    return extract_json(final_text(resp))


def make_story(photo, shop, path):
    W, H = 1080, 1920
    img = fit(photo, W, H)
    d = ImageDraw.Draw(img, "RGBA")
    d.rectangle([0, H - 560, W, H], fill=(255, 252, 247, 238))
    M = 80
    y = H - 500
    d.rounded_rectangle([M, y, M + d.textlength(shop["prefecture"] or "全国", font=font(48)) + 50, y + 80], radius=16, fill=SHU)
    d.text((M + 25, y + 40), shop["prefecture"] or "全国", font=font(48), fill=WHITE, anchor="lm")
    y += 120
    f = font(84, "Black")
    for ln in wrap(d, shop["name"], f, W - 2 * M)[:2]:
        d.text((M, y), ln, font=f, fill=SUMI, anchor="lt")
        y += 105
    if shop.get("rating"):
        d.text((M, y + 20), f"{stars(shop['rating'])} {shop['rating']:.1f}", font=font(56), fill=SHU, anchor="lt")
    img.save(path, "JPEG", quality=90)


def main():
    raws = load_raw()
    p = lookup_place(memo) if memo else None
    address = p["formattedAddress"] if p else ""
    pref = next((x for x in PREFECTURES if x in address), "")
    shop = {
        "name": p["displayName"]["text"] if p else (memo.split()[0] if memo else "おすすめのお店"),
        "prefecture": pref,
        "city": "",
        "genre": p.get("primaryTypeDisplayName", {}).get("text", "") if p else "",
        "rating": round(p["rating"], 1) if p and p.get("rating") else None,
        "reviews": p.get("userRatingCount", 0) if p else 0,
        "address": address,
        "maps_url": p.get("googleMapsUri", "") if p else "",
        "place_id": p["id"] if p else "",
        "trend_reason": f"自分で撮影｜{memo}"[:60],
        "no_story_cta": True,
    }
    gen = write_caption(shop, raws)
    shop.update(catch=gen["catch"], points=gen["points"], caption=gen["caption"])
    shop["genre"] = shop["genre"] or gen.get("genre", "")

    names = []
    for i, img in enumerate(raws, 1):
        fit(img, 1080, 1350).save(out / f"photo_{i}.jpg", "JPEG", quality=90)
        names.append(f"photo_{i}.jpg")
    make_feed_card(shop, out / "feed.jpg")
    make_story(raws[0], shop, out / "story.jpg")
    for f in out.glob("raw_*.jpg"):
        f.unlink()

    shop.update(id=post_id, photos=names + ["feed.jpg"], cover="photo_1.jpg", source="photo")
    (out / "post.json").write_text(json.dumps(shop, ensure_ascii=False, indent=2), encoding="utf-8")

    history = load_history()
    history.append({"id": post_id, "name": shop["name"], "prefecture": pref, "place_id": shop["place_id"], "source": "photo"})
    HISTORY.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"下書き完成: {post_id} {shop['name']}（{len(names)}枚）")


if __name__ == "__main__":
    main()
