"""下書きをLINEに送る（画像＋本文＋「投稿する／ボツ」ボタン）"""
import json
import os
import sys
from pathlib import Path

import requests

post_id = sys.argv[1]
post = json.loads((Path(__file__).parent / "posts" / post_id / "post.json").read_text(encoding="utf-8"))
repo = os.environ["GITHUB_REPOSITORY"]
base = f"https://raw.githubusercontent.com/{repo}/main/posts/{post_id}"
feed = f"{base}/{post.get('cover', 'feed.jpg')}"

flex = {
    "type": "bubble",
    "hero": {"type": "image", "url": feed, "size": "full", "aspectRatio": "4:5", "aspectMode": "cover"},
    "body": {
        "type": "box", "layout": "vertical", "spacing": "sm",
        "contents": [
            {"type": "text", "text": f"{post['prefecture']}｜{post['genre']}", "size": "xs", "color": "#C8442C"},
            {"type": "text", "text": post["name"], "weight": "bold", "size": "lg", "wrap": True},
            {"type": "text", "text": (f"⭐{post['rating']}（{post['reviews']:,}件）" if post.get('rating') else "Googleマップ評価なし") + (f"｜写真{len(post['photos']) - 1}枚＋まとめ" if post.get('photos') else ""), "size": "sm"},
            {"type": "text", "text": post["trend_reason"], "size": "sm", "wrap": True, "color": "#555555"},
        ],
    },
    "footer": {
        "type": "box", "layout": "horizontal", "spacing": "md",
        "contents": [
            {"type": "button", "style": "primary", "color": "#C8442C",
             "action": {"type": "postback", "label": "投稿する", "data": f"approve|{post_id}", "displayText": "投稿する"}},
            {"type": "button", "style": "secondary",
             "action": {"type": "postback", "label": "ボツ", "data": f"reject|{post_id}", "displayText": "ボツ"}},
        ],
    },
}
if post.get("maps_url"):
    flex["body"]["contents"].append(
        {"type": "button", "style": "link", "height": "sm",
         "action": {"type": "uri", "label": "Googleマップで確認", "uri": post["maps_url"]}})

r = requests.post(
    "https://api.line.me/v2/bot/message/push",
    headers={"Authorization": f"Bearer {os.environ['LINE_CHANNEL_ACCESS_TOKEN']}"},
    json={
        "to": os.environ["LINE_USER_ID"],
        "messages": [
            {"type": "text", "text": f"📝 投稿文の下書き\n\n{post['caption']}"},
            {"type": "flex", "altText": f"投稿確認：{post['name']}", "contents": flex},
        ],
    },
    timeout=30,
)
print(r.status_code, r.text)
r.raise_for_status()
