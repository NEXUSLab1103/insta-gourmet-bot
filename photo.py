"""
自分で撮った写真から投稿下書きを作る
入力: posts/<post_id>/raw_*.jpg（LINEから届いた写真）、MEMO（店名・エリア・ひとこと）
出力: photo_1.jpg… + feed.jpg（まとめカード）+ story.jpg + post.json
"""
import base64
import io
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import unquote

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
    if not files:  # 作り直しの時は前回の写真を使う
        files = sorted(out.glob("photo_*.jpg"), key=lambda p: int(p.stem.split("_")[1]))
    if not files:
        sys.exit("写真が見つからへん")
    return [ImageOps.exif_transpose(Image.open(f)).convert("RGB") for f in files[:9]]


URL_RE = re.compile(r"https?://\S+")


def parse_memo(memo):
    """メモからGoogleマップのリンクと、それ以外のコメントを分ける"""
    urls = URL_RE.findall(memo)
    comment = URL_RE.sub("", memo).strip()
    return (urls[0] if urls else None), comment


def resolve_maps_url(url):
    """短縮リンクを展開して、店名と緯度経度を取り出す"""
    try:
        final = requests.get(url, allow_redirects=True, timeout=20, headers={"User-Agent": "Mozilla/5.0"}).url
    except requests.RequestException:
        return None, None
    final = unquote(final)
    name = None
    m = re.search(r"/maps/(?:place|search)/([^/@?]+)", final) or re.search(r"[?&]q=([^&]+)", final)
    if m:
        name = m.group(1).replace("+", " ")
    ll = re.search(r"@(-?\d+\.\d+),(-?\d+\.\d+)", final) or re.search(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)", final)
    return name, ((float(ll.group(1)), float(ll.group(2))) if ll else None)


FIELDS = ("places.id,places.displayName,places.formattedAddress,places.rating,places.userRatingCount,"
          "places.googleMapsUri,places.primaryTypeDisplayName,places.reviews,places.websiteUri")


def lookup_place(query, latlng=None):
    body = {"textQuery": query, "languageCode": "ja", "regionCode": "JP"}
    if latlng:
        body["locationBias"] = {"circle": {"center": {"latitude": latlng[0], "longitude": latlng[1]}, "radius": 300.0}}
    r = requests.post(
        "https://places.googleapis.com/v1/places:searchText",
        headers={"X-Goog-Api-Key": os.environ["GOOGLE_PLACES_API_KEY"], "X-Goog-FieldMask": FIELDS},
        json=body,
        timeout=30,
    )
    r.raise_for_status()
    places = r.json().get("places", [])
    return places[0] if places else None


def find_place(memo):
    url, comment = parse_memo(memo)
    if url:
        name, latlng = resolve_maps_url(url)
        print(f"リンク展開: {name} {latlng}")
        if name:
            p = lookup_place(name, latlng)
            if p:
                return p, comment
    return (lookup_place(comment) if comment else None), comment


def research_reputation(shop):
    """Google口コミ＋Web/SNSの評判を調べて、裏付けのある評価だけまとめる"""
    reviews = "\n".join(
        f"- ★{rv.get('rating')} {rv.get('text', {}).get('text', '')[:300]}" for rv in shop.get("_reviews", [])
    ) or "なし"
    prompt = f"""次のお店の評判を調べてください。

店名: {shop['name']}
住所: {shop.get('address') or '不明'}
Googleマップ評価: {shop.get('rating') or 'なし'}（{shop.get('reviews') or 0}件）
Googleマップの口コミ（一部）:
{reviews}

やること:
1. Web検索で、このお店についての Instagram・X・TikTok・食べログ・ブログ・ニュースなどの情報を探す（同名の別店舗と混同しないよう住所で確認）
2. Googleの口コミとWeb/SNSの情報から、複数の情報源で共通して言われている評価・人気メニュー・お店の特徴だけを抜き出す

厳守:
- 確認できた事実だけを書く。推測・想像・「〜と思われる」は書かない
- 口コミの文章をそのまま引用しない。自分の言葉で要約する
- 情報が少なければ、少ないまま返す（無理に埋めない）

次のJSONだけで返してください:
```json
{{"recommend": ["おすすめポイント（16字以内）", "…最大3つ。根拠のあるものだけ"], "summary": "評判のまとめ（2〜3文。どこで何と言われているか）", "popular_menu": ["評判で名前が挙がっている具体的なメニュー名（確認できたものだけ）"], "sources": ["参考にした情報源の種類（例: Googleマップ口コミ, Instagram, 食べログ）"]}}
```"""
    resp = client.messages.create(
        model=MODEL,
        max_tokens=4000,
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 6}],
        messages=[{"role": "user", "content": prompt}],
    )
    return extract_json(final_text(resp))


def b64(img):
    small = img.copy()
    small.thumbnail((1024, 1024))
    buf = io.BytesIO()
    small.save(buf, "JPEG", quality=80)
    return base64.b64encode(buf.getvalue()).decode()


def write_caption(shop, photos, comment, rep):
    prompt = f"""Instagramのグルメ紹介アカウント「全国バズグルメ」の投稿文を作ってください。
アカウント運営者が実際にお店に行って撮った写真です。

店名: {shop['name']}
住所: {shop.get('address') or '不明'}
ジャンル: {shop.get('genre') or '不明'}
Googleマップ評価: {shop.get('rating') or 'なし'}（{shop.get('reviews') or 0}件）
運営者のひとこと: {comment or 'なし'}
調査した評判: {json.dumps(rep, ensure_ascii=False)}

構成:
1. 1行目：思わず読みたくなるフック（事実ベース）
2. 店名とエリア
3. 写真で紹介している料理：料理名は「調査した評判のpopular_menu」か「運営者のひとこと」で確認できたものだけ使う。確認できない場合は「パスタ」「肉料理」のような大きなくくりで呼ぶ
4. 「みんなの評判」：調査した評判のsummaryをもとに、Googleマップや SNS でどう評価されているかを書く
5. 最後に「ここがおすすめ」として recommend を箇条書き
6. 評価があれば「⭐Googleマップ評価 X（N件・投稿時点）」
7. 「※最新の営業情報はお店の公式情報をご確認ください」
8. ハッシュタグ10〜15個（都道府県が分かれば #〇〇グルメ を含める）

厳守:
- 推測・想像は一切書かない。「〜のような」「〜っぽい」「〜かも」で食材や味を想像して書かない
- 写真から言えるのは「見た目」だけ（例: チーズたっぷり）。食材の断定、味、量の評価はしない
- 運営者のひとことにない感想を、運営者の感想として書かない
- 価格・営業時間・予約可否など、確認できない情報は書かない
- 口コミの文章をそのまま引用しない

次のJSONだけで返してください:
```json
{{"catch": "画像に載せる短いキャッチ（18字以内・事実ベース）", "genre": "ジャンル（10字以内）", "caption": "投稿本文（ハッシュタグ含む）"}}
```"""
    content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64(p)}} for p in photos[:4]]
    content.append({"type": "text", "text": prompt})
    resp = client.messages.create(model=MODEL, max_tokens=2500, messages=[{"role": "user", "content": content}])
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
    p, comment = find_place(memo) if memo else (None, "")
    address = p["formattedAddress"] if p else ""
    pref = next((x for x in PREFECTURES if x in address), "")
    shop = {
        "name": p["displayName"]["text"] if p else (comment.split()[0] if comment else "おすすめのお店"),
        "prefecture": pref,
        "city": "",
        "genre": p.get("primaryTypeDisplayName", {}).get("text", "") if p else "",
        "rating": round(p["rating"], 1) if p and p.get("rating") else None,
        "reviews": p.get("userRatingCount", 0) if p else 0,
        "address": address,
        "maps_url": p.get("googleMapsUri", "") if p else "",
        "place_id": p["id"] if p else "",
        "no_story_cta": True,
        "_reviews": (p or {}).get("reviews", [])[:5],
    }
    rep = research_reputation(shop)
    gen = write_caption(shop, raws, comment, rep)
    shop.pop("_reviews", None)
    points = [x for x in rep.get("recommend", []) if x][:3]
    shop.update(catch=gen["catch"], points=points, caption=gen["caption"], reputation=rep, points_title="ここがおすすめ（Googleマップ・SNSの評判より）")
    shop["genre"] = shop["genre"] or gen.get("genre", "")
    shop["trend_reason"] = (rep.get("summary") or f"自分で撮影｜{comment}")[:80]

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
