"""
バズグルメ自動投稿ボット：下書き生成
1. 投稿数が少ない県を選ぶ（県別ハイライト用に偏りなく回す）
2. Claude が Web検索で「今SNSで話題の店」を候補3つ探す
3. Google Places API で評価・口コミ数・営業状況を確認
4. Claude がキャプション作成
5. フィード用・ストーリーズ用の画像カードを生成して posts/<id>/ に保存
"""
import json
import os
import random
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import anthropic
import requests

from card import make_feed_card, make_story_card

ROOT = Path(__file__).parent
HISTORY = ROOT / "data" / "history.json"
POSTS = ROOT / "posts"
JST = timezone(timedelta(hours=9))

MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")
MIN_RATING = float(os.environ.get("MIN_RATING", "3.6"))
MIN_REVIEWS = int(os.environ.get("MIN_REVIEWS", "30"))

PREFECTURES = [
    "北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県",
    "茨城県", "栃木県", "群馬県", "埼玉県", "千葉県", "東京都", "神奈川県",
    "新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県", "岐阜県",
    "静岡県", "愛知県", "三重県", "滋賀県", "京都府", "大阪府", "兵庫県",
    "奈良県", "和歌山県", "鳥取県", "島根県", "岡山県", "広島県", "山口県",
    "徳島県", "香川県", "愛媛県", "高知県", "福岡県", "佐賀県", "長崎県",
    "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県",
]

client = anthropic.Anthropic()


def load_history():
    if HISTORY.exists():
        return json.loads(HISTORY.read_text(encoding="utf-8"))
    return []


def pick_prefecture(history):
    forced = os.environ.get("PREFECTURE", "").strip()
    if forced:
        return forced
    counts = {p: 0 for p in PREFECTURES}
    for h in history:
        if h.get("prefecture") in counts:
            counts[h["prefecture"]] += 1
    low = min(counts.values())
    recent = {h.get("prefecture") for h in history[-5:]}
    pool = [p for p, c in counts.items() if c == low and p not in recent]
    return random.choice(pool or [p for p, c in counts.items() if c == low])


def extract_json(text):
    m = re.search(r"```json\s*(.*?)```", text, re.S) or re.search(r"(\{.*\}|\[.*\])", text, re.S)
    if not m:
        raise ValueError("JSONが見つからへん:\n" + text)
    return json.loads(m.group(1))


def final_text(resp):
    return "".join(b.text for b in resp.content if b.type == "text")


def find_candidates(pref, exclude):
    today = datetime.now(JST).strftime("%Y年%m月%d日")
    prompt = f"""今日は{today}です。{pref}で、ここ最近InstagramやTikTok、Xなどで話題になっている飲食店を探してください。

条件:
- 実在し、現在営業中の店
- 新店・行列店・SNSでバズったメニューがある店など「今」話題の理由が説明できる店
- 季節感のある話題なら尚よし
- 次の店は除外: {", ".join(exclude) or "なし"}

Web検索で裏付けを取ったうえで、候補を3つ、次のJSONだけで返してください:
```json
[{{"name": "店名", "city": "市区町村", "genre": "ジャンル", "trend_reason": "話題の理由（事実ベース・40字以内）"}}]
```"""
    resp = client.messages.create(
        model=MODEL,
        max_tokens=4000,
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 6}],
        messages=[{"role": "user", "content": prompt}],
    )
    return extract_json(final_text(resp))


def lookup_place(c, pref):
    r = requests.post(
        "https://places.googleapis.com/v1/places:searchText",
        headers={
            "X-Goog-Api-Key": os.environ["GOOGLE_PLACES_API_KEY"],
            "X-Goog-FieldMask": "places.id,places.displayName,places.formattedAddress,"
            "places.rating,places.userRatingCount,places.googleMapsUri,"
            "places.businessStatus,places.primaryTypeDisplayName",
        },
        json={"textQuery": f"{c['name']} {pref} {c.get('city', '')}", "languageCode": "ja", "regionCode": "JP"},
        timeout=30,
    )
    r.raise_for_status()
    places = r.json().get("places", [])
    if not places:
        return None
    p = places[0]
    if p.get("businessStatus") != "OPERATIONAL" or pref not in p.get("formattedAddress", ""):
        return None
    if p.get("rating", 0) < MIN_RATING or p.get("userRatingCount", 0) < MIN_REVIEWS:
        return None
    return p


def write_caption(shop):
    prompt = f"""Instagramのグルメ紹介アカウントの投稿文を作ってください。

店名: {shop['name']}
エリア: {shop['prefecture']} {shop['city']}
ジャンル: {shop['genre']}
話題の理由: {shop['trend_reason']}
Googleマップ評価: {shop['rating']}（口コミ{shop['reviews']}件）

ルール:
- 親しみやすい口調、絵文字は控えめ
- 口コミ本文の引用・転載はしない。事実と話題の理由を自分の言葉で
- 「体にいい」「〇〇に効く」等の健康効果、断定的な最上級表現（日本一など）は書かない
- 価格や営業時間など確認できない情報は書かない
- 本文の最後に「⭐Googleマップ評価 {shop['rating']}（{shop['reviews']}件・投稿時点）」と「※最新の営業情報はお店の公式情報をご確認ください」を入れる
- ハッシュタグは10〜15個。必ず #{shop['prefecture'].rstrip('都道府県')}グルメ #{shop['prefecture']}グルメ を含める

次のJSONだけで返してください:
```json
{{"catch": "画像に載せる短いキャッチ（18字以内）", "points": ["推しポイント1（16字以内）", "推しポイント2", "推しポイント3"], "caption": "投稿本文（ハッシュタグ含む）"}}
```"""
    resp = client.messages.create(model=MODEL, max_tokens=2000, messages=[{"role": "user", "content": prompt}])
    return extract_json(final_text(resp))


def main():
    history = load_history()
    pref = pick_prefecture(history)
    exclude = [h["name"] for h in history if h.get("prefecture") == pref]
    print(f"対象: {pref}")

    shop = None
    for attempt in range(2):
        for c in find_candidates(pref, exclude):
            p = lookup_place(c, pref)
            print(f"  候補 {c['name']} -> {'OK' if p else 'NG'}")
            if p:
                shop = {
                    "name": p["displayName"]["text"],
                    "prefecture": pref,
                    "city": c.get("city", ""),
                    "genre": c.get("genre") or p.get("primaryTypeDisplayName", {}).get("text", ""),
                    "trend_reason": c["trend_reason"],
                    "rating": round(p["rating"], 1),
                    "reviews": p["userRatingCount"],
                    "address": p["formattedAddress"],
                    "maps_url": p.get("googleMapsUri", ""),
                    "place_id": p["id"],
                }
                break
            exclude.append(c["name"])
        if shop:
            break
    if not shop:
        print("条件に合う店が見つからへんかった")
        sys.exit(1)

    shop.update(write_caption(shop))
    post_id = datetime.now(JST).strftime("%Y%m%d-%H%M")
    out = POSTS / post_id
    out.mkdir(parents=True, exist_ok=True)
    make_feed_card(shop, out / "feed.jpg")
    make_story_card(shop, out / "story.jpg")
    shop["id"] = post_id
    (out / "post.json").write_text(json.dumps(shop, ensure_ascii=False, indent=2), encoding="utf-8")

    history.append({"id": post_id, "name": shop["name"], "prefecture": pref, "place_id": shop["place_id"]})
    HISTORY.parent.mkdir(exist_ok=True)
    HISTORY.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")

    if gh_out := os.environ.get("GITHUB_OUTPUT"):
        with open(gh_out, "a") as f:
            f.write(f"post_id={post_id}\n")
    print(f"下書き完成: {post_id} {shop['name']}")


if __name__ == "__main__":
    main()
