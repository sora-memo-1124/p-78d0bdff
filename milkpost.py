"""milkポスト 本体（裏方）

・M!LK 公式 YouTube を数分ごとに見回る
・生配信はすぐ、ふつうの動画は 8時・12時・21時 にまとめて ntfy でスマホに通知
・しめきり帳（1週間前・3日前・前の日・当日の朝 に通知）
・Googleニュースから M!LK のニュースを10分ごとに集めて、画面に出す（通知はしない）
・同じしくみで、ライブ情報（公式サイトのお知らせをふくむ）も集めて「🎤 ライブ」に出す（通知はしない）
・同じしくみで、出演情報（テレビ・ドラマ・雑誌など）も集めて「📺 出演」に出す（通知はしない）
・よてい帳：配信・テレビ・ドラマ・ラジオの予定（毎週くりかえしもOK）。前の日の夜・30分前・10分前・はじまる時間に通知。
  YouTube の配信予約は、見つけたら自動でよてい帳に入れる
・画面（app.html）をブラウザで開く

起動:  python3 milkpost.py
テスト用: python3 milkpost.py --dry-run --data <ファイル> --port <番号> --no-browser
   --dry-run  … 通知を本当には送らず、記録だけする
"""

import argparse
import datetime as dt
import email.utils
import html
import json
import pathlib
import re
import threading
import time
import urllib.parse
import urllib.error
import urllib.request
import uuid
import webbrowser
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

FOLDER = pathlib.Path(__file__).resolve().parent
APP_HTML = FOLDER / "app.html"
SETTEI = FOLDER / "settei.json"

CHANNELS = {"M!LK 公式": "UCHqQCpqvSOcHQr6Oqa4_5wg"}
# メンバー個人の YouTube（2026年11月15日 追加）。見回りで新しい動画を集めて画面に出すだけ。通知はしない。
# 出どころは 10_メンバーリンク調べ.md（じんだいチャンネルは吉田仁人さんと塩﨑太智さんの2人のチャンネル）
MEMBER_CHANNELS = {
    "じんだいチャンネル": "UCk8oGFgksVxjh2YiU_D6hFQ",
    "佐野勇斗だぞ": "UCKBT9RsmIdJDxoNXUIXvX6A",
    "I'm 曽野舜太": "UCWWcLG54g_jtOaRMxs_KsWQ",
    "劇場版 じゅうたろう": "UC7YwvfJJevPlbjD_KKx1pkQ",
}
CHECK_EVERY_SEC = 3 * 60          # 見回りの間かく（3分。2026-11-17 に5分から変更。公式もメンバー個人の YouTube も）
BATCH_HOURS = (8, 12, 21)         # まとめ通知の時間
REMIND_DAYS = (7, 3, 1, 0)        # 締め切りの何日前に知らせるか
REMIND_HOUR = 8                   # 締め切りのお知らせは朝8時から
MAX_ITEMS = 100                   # 新着一覧に残す数

# ニュース（2026年11月14日 追加）：Googleニュースの「お知らせ窓口（RSS）」で M!LK の記事を集める
NEWS_URL = "https://news.google.com/rss/search?q=%22M%21LK%22&hl=ja&gl=JP&ceid=JP:ja"
NEWS_EVERY_SEC = 10 * 60          # ニュースを見に行く間かく（10分。2026-11-16 に30分から変更。「いま見回る」を押したときは、すぐ見る）
MAX_NEWS = 100                    # ニュース一覧に残す数

# ライブ情報（2026年11月14日 追加・案あ）：「M!LK」と「ツアー・ライブ・公演・チケット・コンサート」の記事を集める。
# M!LK 公式サイトのお知らせも Googleニュースにのるので、それも入る（公式サイトには直接読みに行かない）
LIVE_URL = ("https://news.google.com/rss/search?q=%22M%21LK%22%20(%E3%83%84%E3%82%A2%E3%83%BC%20OR%20"
            "%E3%83%A9%E3%82%A4%E3%83%96%20OR%20%E5%85%AC%E6%BC%94%20OR%20%E3%83%81%E3%82%B1%E3%83%83%E3%83%88"
            "%20OR%20%E3%82%B3%E3%83%B3%E3%82%B5%E3%83%BC%E3%83%88)&hl=ja&gl=JP&ceid=JP:ja")
LIVE_WORDS = re.compile(r"TOUR|ツアー|公演|ライブ|LIVE|チケット|先行|機材席|コンサート|会場|グッズ", re.I)

# 出演情報（2026年11月14日 追加）：テレビ・ドラマ・雑誌の表紙など。公式サイトの「放送決定！」「表紙決定！」も入る
APPEAR_URL = ("https://news.google.com/rss/search?q=%22M%21LK%22%20%28%E5%87%BA%E6%BC%94%20OR%20%E6%94%BE%E9%80%81"
              "%20OR%20%E7%95%AA%E7%B5%84%20OR%20%E9%9B%91%E8%AA%8C%20OR%20%E8%A1%A8%E7%B4%99%20OR%20%E3%83%A9%E3%82%B8"
              "%E3%82%AA%20OR%20%E3%83%89%E3%83%A9%E3%83%9E%20OR%20%E3%82%AA%E3%83%B3%E3%82%A8%E3%82%A2%29&hl=ja&gl=JP&ceid=JP:ja")
APPEAR_WORDS = re.compile(r"出演|放送|番組|雑誌|表紙|ラジオ|ドラマ|オンエア|MC|ゲスト|生出演|地上波")

# 番組の宣伝・インタビュー・動画の記事（2026年11月14日 追加）。ニュースの「🎥 動画・インタビュー」に出す
VIDEO_URL = ("https://news.google.com/rss/search?q=" + "%22M%21LK%22%20%28%E3%82%A4%E3%83%B3%E3%82%BF%E3%83%93%E3%83%A5%E3%83%BC"
             "%20OR%20%E4%BA%88%E5%91%8A%20OR%20%E7%95%AA%E5%AE%A3%20OR%20%E5%8B%95%E7%94%BB%20OR%20%E6%98%A0%E5%83%8F"
             "%20OR%20%E3%83%A1%E3%82%A4%E3%82%AD%E3%83%B3%E3%82%B0%20OR%20TVer%29&hl=ja&gl=JP&ceid=JP:ja")
VIDEO_BROKEN = re.compile(r"互換性のあるソース|ページが見つかりません|お探しのページ|エラーが発生")   # こわれたページの題名
TVER_KEEP_DAYS = 14   # TVer の見のがし配信は短い間しか見られないので、2週間より古い TVer のページは出さない（2026年11月14日）


def video_ok(n, t=None):
    """動画・インタビューの一覧に出してよい記事か。こわれた題名と、古い TVer のページは出さない。"""
    if VIDEO_BROKEN.search(n.get("title", "")):
        return False
    if n.get("source") == "TVer":
        try:
            pub = dt.datetime.fromisoformat(n.get("published", "")).astimezone().replace(tzinfo=None)
        except (TypeError, ValueError):
            return False
        return pub >= (t or now()) - dt.timedelta(days=TVER_KEEP_DAYS)
    return True


VIDEO_WORDS = re.compile(r"インタビュー|予告|番宣|動画|映像|メイキング|メーキング|TVer|YouTube|CM|ムービー|オフショット")

# よてい帳を自動でうめるための、予定さがし専用の記事（画面には出さない。2026年11月14日 追加）
SCHED_QUERIES = ('"M!LK" 毎週', '"M!LK" (生出演 OR 生放送 OR 今夜 OR 今晩 OR 本日 OR 放送日 OR 初回放送)')

NS = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
UA = {"User-Agent": "milkpost/1.0 (personal notifier)", "Accept-Language": "ja"}

LOCK = threading.RLock()
ARGS = None


# ---------------- 記録（data.json） ----------------

def empty_data():
    return {
        "last_check": None,       # 最後に見回った時間
        "last_error": None,       # 見回りでうまくいかなかったこと
        "items": [],              # 新着一覧
        "seen": [],               # もう見つけた動画のID
        "pending_batch": [],      # まとめ通知を待っている動画のID
        "batch_sent": [],         # 送ったまとめ通知の枠（例 "2026-11-13 08"）
        "deadlines": [],          # しめきり帳
        "notify_log": [],         # 送った通知の控え（新しいもの50件）
        "news": [],               # ニュース一覧（通知はしない）
        "news_seen": [],          # もう見つけたニュースのID
        "news_check": None,       # 最後にニュースを見に行った時間
        "news_error": None,       # ニュースでうまくいかなかったこと
        "live": [],               # ライブ情報の一覧（通知はしない）
        "live_seen": [],          # もう見つけたライブ情報のID
        "appear": [],             # 出演情報の一覧（通知はしない）
        "appear_seen": [],        # もう見つけた出演情報のID
        "events": [],             # よてい帳（配信・テレビ・ドラマ・ラジオの予定）
        "sched": [],              # 予定さがし専用の記事（画面には出さない）
        "sched_seen": [],
        "video": [],              # 番組の宣伝・インタビュー・動画の記事
        "video_seen": [],
        "member_yt": {},          # メンバー個人の YouTube の新しい動画 {チャンネル名: [動画…]}（通知はしない）
        "official": [],           # 公式サイト（sd-milk.com）の NEWS を直接読んだもの（2026-11-18 追加）
        "official_seen": [],
    }


def load():
    p = ARGS.data
    if p.exists():
        d = json.loads(p.read_text(encoding="utf-8"))
        base = empty_data()
        base.update(d)
        return base
    return empty_data()


def save(d):
    p = ARGS.data
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)   # 途中で止まっても記録が壊れないように、書き終えてから入れかえる


# ---------------- 通知（ntfy） ----------------

def topic():
    return json.loads(SETTEI.read_text(encoding="utf-8"))["ntfy_topic"]


def notify(d, title, message, url=None, urgent=False):
    entry = {"time": now().strftime("%Y-%m-%d %H:%M"), "title": title, "message": message,
             "url": url, "urgent": urgent, "sent": False}
    if ARGS.dry_run:
        entry["sent"] = "テスト（送っていない）"
    else:
        body = {"topic": topic(), "title": title, "message": message,
                "priority": 5 if urgent else 3, "tags": ["milk_glass"]}
        if url:
            body["click"] = url
        req = urllib.request.Request("https://ntfy.sh/", data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=15):
                entry["sent"] = True
        except Exception as e:  # 通知に失敗しても、アプリは止めない
            entry["sent"] = False
            entry["error"] = str(e)
    d["notify_log"] = ([entry] + d["notify_log"])[:50]
    return entry["sent"]


# ---------------- 見回り ----------------

def now():
    return dt.datetime.now()


def fetch(url, timeout=20, tries=4):
    # YouTube などが、ときどき 404・429・500 を返すので、少し待ってやり直す（2026年11月16日 追加）
    for i in range(tries):
        req = urllib.request.Request(url, headers=UA)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as res:
                return res.read()
        except urllib.error.HTTPError as e:
            if e.code not in (404, 429, 500, 502, 503) or i == tries - 1:
                raise
            time.sleep(1 + i)


def youtube_feed(channel_id):
    root = ET.fromstring(fetch(f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"))
    for e in root.findall("a:entry", NS):
        yield {
            "id": e.findtext("yt:videoId", namespaces=NS),
            "title": e.findtext("a:title", namespaces=NS) or "",
            "url": e.find("a:link", NS).get("href"),
            "published": e.findtext("a:published", namespaces=NS) or "",
        }


UPCOMING = {}   # 配信予約（これから始まる生配信・プレミア公開）の開始時間 {動画ID: "2026-11-20T20:00"}


def is_live(video_id):
    """YouTube のページにある「生配信かどうか」の印を見る。分からなければ False。
    配信予約（これから始まる）なら、開始時間も UPCOMING にメモする（2026年11月14日 追加）。"""
    try:
        html = fetch(f"https://www.youtube.com/watch?v={video_id}").decode("utf-8", "ignore")
    except Exception:
        return False
    m = re.search(r'"isLiveContent":(true|false)', html)
    start = None
    s = re.search(r'"scheduledStartTime":"(\d{9,11})"', html)
    if s:
        start = dt.datetime.fromtimestamp(int(s.group(1)))
    else:
        s = re.search(r'"startTimestamp":"([0-9T:\-+.Z]+)"', html)
        if s and '"isUpcoming":true' in html:
            try:
                start = dt.datetime.fromisoformat(s.group(1).replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
            except ValueError:
                start = None
    if start and start > now():
        UPCOMING[video_id] = start.strftime("%Y-%m-%dT%H:%M")
    return bool(m and m.group(1) == "true")


def collect(seen, live_checker=is_live, feed=youtube_feed):
    """インターネットを見回って、まだ見ていない動画を集める（記録にはまだ書かない）。"""
    seen = set(seen)
    found = []
    for name, cid in CHANNELS.items():
        for v in feed(cid):
            if v["id"] in seen:
                continue
            seen.add(v["id"])
            kind = "配信" if live_checker(v["id"]) else "動画"
            found.append({**v, "source": name, "kind": kind,
                          "found_at": now().strftime("%Y-%m-%d %H:%M")})
            if v["id"] in UPCOMING:
                found[-1]["start"] = UPCOMING.pop(v["id"])   # 配信予約の開始時間
    return found


def check(d, live_checker=is_live, feed=youtube_feed):
    """1回見回る。見つけた新しい動画の数を返す。"""
    return apply_found(d, collect(d["seen"], live_checker, feed))


def apply_found(d, found):
    """集めた動画を記録して、必要なら通知する。"""
    first_time = not d["seen"]
    seen = set(d["seen"])
    found = [v for v in found if v["id"] not in seen]   # 見回り中に別の見回りで記録された分は除く
    seen.update(v["id"] for v in found)
    d["seen"] = sorted(seen)
    if found:
        found.sort(key=lambda x: x["published"], reverse=True)
        d["items"] = (found + d["items"])[:MAX_ITEMS]
        for v in found:   # 配信予約は、よてい帳に自動で入れる（10分前とはじまる時間にお知らせ）
            if v.get("start"):
                date, time_ = v["start"].split("T")
                if add_event(d, v["title"][:100], "youtube", date, time_, "none", v["url"], ["10", "start"],
                             auto=True, src=v["id"]) and not first_time:
                    sd = dt.datetime.fromisoformat(v["start"])
                    notify(d, f"📅 M!LK 配信予定 {sd.month}月{sd.day}日 {time_}", v["title"], v["url"])
        if not first_time:   # はじめての見回りでは、いまある動画を覚えるだけ（通知しない）
            for v in found:
                if v.get("start"):
                    continue   # 配信予約は、上でよてい帳に入れた
                if v["kind"] == "配信":
                    notify(d, "🔴 M!LK 生配信", v["title"], v["url"], urgent=True)
                else:
                    d["pending_batch"].append(v["id"])
    d["last_check"] = now().strftime("%Y-%m-%d %H:%M")
    d["last_error"] = None
    return len(found)


def news_feed(url=None):
    """Googleニュースから M!LK の記事を集める（新しいもの順でなくてもよい）。"""
    root = ET.fromstring(fetch(url or NEWS_URL))
    out = []
    for i in root.findall(".//item"):
        title = (i.findtext("title") or "").strip()
        source = (i.findtext("source") or "").strip()
        if source and title.endswith(" - " + source):
            title = title[: -len(source) - 3].strip()
        if re.match(r"^(画像・写真|関連写真|写真)\s*[|｜]", title) or "関連画像" in title:
            continue   # 写真だけの記事はのぞく
        try:
            pub = email.utils.parsedate_to_datetime(i.findtext("pubDate")).astimezone()
            published = pub.isoformat(timespec="seconds")   # 例 2026-11-13T23:31:38+09:00
        except Exception:
            published = ""
        link = (i.findtext("link") or "").strip()
        out.append({"id": (i.findtext("guid") or link).strip(), "title": title, "source": source,
                    "url": link, "published": published})
    return out


def apply_news(d, fetched):
    """集めたニュースを記録する。新しく見つけた数を返す。"""
    seen = set(d["news_seen"])
    new = [n for n in fetched if n["id"] and n["id"] not in seen]
    stamp = now().strftime("%Y-%m-%d %H:%M")
    for n in new:
        n["found_at"] = stamp
    allnews = new + d["news"]
    allnews.sort(key=lambda x: x["published"], reverse=True)
    d["news"] = allnews[:MAX_NEWS]
    d["news_seen"] = (sorted(seen | {n["id"] for n in new}))[-1000:]
    d["news_check"] = stamp
    d["news_error"] = None
    return len(new)


def topic_feed(url, words, keep_official=False):
    """ライブ・出演など、話題をしぼった記事を集める。
    題名に話題のことばがないものはのぞく（keep_official なら、公式サイトのお知らせはことばがなくても残す）。"""
    out = []
    for n in news_feed(url):
        n["official"] = "オフィシャル" in n["source"]   # M!LK オフィシャルサイトのお知らせ
        if not (words.search(n["title"]) or (keep_official and n["official"])):
            continue
        out.append(n)
    return out


def live_feed():
    """ライブ情報を集める。"""
    return topic_feed(LIVE_URL, LIVE_WORDS)


def appear_feed():
    """出演情報（テレビ・ドラマ・雑誌など）を集める。"""
    return topic_feed(APPEAR_URL, APPEAR_WORDS, keep_official=True)


def apply_topic(d, key, fetched):
    """集めた記事を d[key] に記録する（同じものは足さない）。新しく見つけた数を返す。"""
    seen = set(d[key + "_seen"])
    new = [n for n in fetched if n["id"] and n["id"] not in seen]
    stamp = now().strftime("%Y-%m-%d %H:%M")
    for n in new:
        n["found_at"] = stamp
    allitems = new + d[key]
    allitems.sort(key=lambda x: x["published"], reverse=True)
    d[key] = allitems[:MAX_NEWS]
    d[key + "_seen"] = (sorted(seen | {n["id"] for n in new}))[-1000:]
    return len(new)


# 公式サイトの NEWS（2026年11月18日 追加）：sd-milk.com/contents/news の一覧を直接読む（1〜2ページ目＝新しい約20件）
OFFICIAL_NEWS_PAGES = ["https://sd-milk.com/contents/news", "https://sd-milk.com/contents/news/page/2"]
OFFICIAL_ITEM_RE = re.compile(r'<a href="/contents/(\d+)"[^>]*class="c_contents-list-anchor"[^>]*>(.*?)</a>', re.S)


def official_feed(pages=None):
    out, ids = [], set()
    for i, url in enumerate(pages or OFFICIAL_NEWS_PAGES):
        try:
            page = fetch(url).decode("utf-8", "replace")
        except Exception as e:
            if i == 0:
                raise
            print("公式サイト2ページ目のエラー:", e)
            continue
        for m in OFFICIAL_ITEM_RE.finditer(page):
            cid, body = m.group(1), m.group(2)
            dm = re.search(r'datetime="(\d{4}-\d{2}-\d{2})"', body)
            tm = re.search(r"<h2[^>]*>(.*?)</h2>", body, re.S)
            if not dm or not tm or cid in ids:
                continue
            ids.add(cid)
            title = html.unescape(re.sub(r"<[^>]+>", "", tm.group(1))).strip()
            cats = [html.unescape(c).strip() for c in re.findall(r'<span class="tag">(.*?)</span>', body)]
            out.append({"id": "sd:" + cid, "title": title, "url": "https://sd-milk.com/contents/" + cid,
                        "published": dm.group(1) + "T00:00:00+09:00", "cats": cats,
                        "source": "M!LKオフィシャルサイト", "official": True})
    return out


def apply_live(d, fetched):
    return apply_topic(d, "live", fetched)


def send_batch_if_time(d, t=None):
    """8時・12時・21時の枠の中なら、たまっている動画をまとめて通知する。"""
    t = t or now()
    if t.hour not in BATCH_HOURS:
        return False
    slot = t.strftime("%Y-%m-%d %H")
    if slot in d["batch_sent"]:
        return False
    d["batch_sent"] = (d["batch_sent"] + [slot])[-30:]
    ids = d["pending_batch"]
    if not ids:
        return False
    vids = [i for i in d["items"] if i["id"] in ids]
    d["pending_batch"] = []
    if not vids:
        return False
    lines = "\n".join("・" + v["title"][:40] for v in vids[:10])
    notify(d, f"🥛 M!LK 新しい動画 {len(vids)}本", lines, vids[0]["url"])
    return True


# ---------------- よてい帳（2026年11月14日 追加） ----------------

EVENT_KINDS = {"youtube": "▶ YouTube", "tv": "📺 テレビ", "drama": "🎬 ドラマ", "radio": "📻 ラジオ", "sns": "📱 SNS配信", "live": "🎤 コンサート"}
EVENT_NOTIFY = ("eve", "30", "10", "start")   # 前の日の夜（20時）・30分前・10分前・はじまる時間
EVE_HOUR = 20


def add_event(d, name, kind, date, time_, repeat="none", url="", notify_keys=("30", "start"), auto=False, src=None):
    """よてい帳に1つ入れる。同じものがもうあれば入れない。入れたら True。"""
    dt.datetime.fromisoformat(f"{date}T{time_}")   # 日にちと時間の形を確かめる（だめならエラー）
    for e in d["events"]:
        if (src and e.get("src") == src) or (e["name"] == name and e["date"] == date and e["time"] == time_):
            return False
    d["events"].append({"id": uuid.uuid4().hex[:8], "name": name[:100], "kind": kind if kind in EVENT_KINDS else "tv",
                        "date": date, "time": time_, "repeat": "weekly" if repeat == "weekly" else "none",
                        "url": (url or "")[:500], "notify": [k for k in notify_keys if k in EVENT_NOTIFY],
                        "notified": [], "auto": auto, "src": src})
    return True


def next_occurrence(e, after):
    """after より後（または同じ）で、いちばん近い予定の時刻。1回だけの予定は、その時刻をそのまま返す。"""
    start = dt.datetime.fromisoformat(f"{e['date']}T{e['time']}")
    if e.get("repeat") != "weekly" or start >= after:
        return start
    weeks = (after - start).days // 7
    occ = start + dt.timedelta(weeks=weeks)
    while occ < after:
        occ += dt.timedelta(weeks=1)
    return occ


def send_event_reminders(d, t=None):
    """よてい帳のお知らせを送る。送った数を返す。"""
    t = t or now()
    count = 0
    keep = []
    for e in d["events"]:
        occ = next_occurrence(e, t - dt.timedelta(minutes=15))
        if e.get("repeat") != "weekly" and occ < t - dt.timedelta(days=7):
            continue   # 1週間より前に終わった1回だけの予定は、よてい帳から消す
        keep.append(e)
        label = EVENT_KINDS.get(e["kind"], "")
        when = f"{occ.month}月{occ.day}日 {occ:%H:%M}"
        for k in e.get("notify", []):
            key = f"{occ:%Y-%m-%d %H:%M}|{k}"
            if key in e.setdefault("notified", []):
                continue
            if k == "eve":
                ok = t.date() == (occ - dt.timedelta(days=1)).date() and t.hour >= EVE_HOUR
                title = f"🗓 あした {occ:%H:%M} から {label}"
            elif k in ("30", "10"):
                ok = occ - dt.timedelta(minutes=int(k)) <= t < occ
                title = f"⏰ もうすぐ！ {occ:%H:%M} {label}（{k}分前）"
            else:   # start
                ok = occ <= t < occ + dt.timedelta(minutes=15)
                title = f"🔴 はじまるよ！ {label}"
            if not ok:
                continue
            e["notified"] = (e["notified"] + [key])[-50:]
            notify(d, title, f"{e['name']}（{when}）", e.get("url") or None, urgent=(k in ("10", "start")))
            count += 1
    d["events"] = keep
    return count


ART_DATE = re.compile(r"(\d{1,2})月(\d{1,2})日[（(]?[月火水木金土日]?[)）]?\s*([^\d\s]{0,6})?(\d{1,2})(?:時(?!間)(\d{1,2})?分?|:(\d{2}))")
ART_SLASH = re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})[^\d]{0,8}?(\d{1,2}):(\d{2})")
ART_REL = re.compile(r"(本日|今夜|今日|今晩|あす|明日)([^。\d]{0,12}?)(\d{1,2})(?:時(?!間)(\d{1,2})?分?|:(\d{2}))")
ART_WEEKLY = re.compile(r"毎週\s*[（(]?([月火水木金土日])(?:曜日|曜)?[）)]?([^0-9。]{0,8}?)(\d{1,2})(?:時(?!間)(\d{1,2})?分?|:(\d{2}))")
PM_WORDS = ("午後", "夜", "よる", "深夜")


def _pm(hh, words):
    """「午後2時」「夜9時」「深夜1時」のように書いてあれば、24時間の書き方にする。
    深夜の0〜4時と、夜の0〜4時は、次の日の0〜4時（＝24〜28時）にする。（2026年11月14日 直した：前は「深夜1時」が昼の13時になっていた）"""
    w = words or ""
    if "深夜" in w:
        return hh + 24 if hh <= 4 else (hh + 12 if hh < 12 else hh)
    if "午後" in w:
        return hh + 12 if hh < 12 else hh
    if any(x in w for x in ("夜", "よる", "今晩")):
        return hh + 24 if hh <= 4 else (hh + 12 if hh < 12 else hh)
    return hh


def article_time(title, published, t):
    """記事の題名から、放送・配信の日時を読み取る。読み取れなければ None。（2026年11月14日 追加）"""
    try:
        pub = dt.datetime.fromisoformat(published).astimezone().replace(tzinfo=None) if published else t
    except ValueError:
        pub = t
    m = ART_DATE.search(title)
    if m:
        mo, da = int(m.group(1)), int(m.group(2))
        hh, mi = _pm(int(m.group(4)), m.group(3)), int(m.group(5) or m.group(6) or 0)
    else:
        m = ART_SLASH.search(title)
        if m:
            mo, da, hh, mi = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
        else:
            m = ART_REL.search(title)
            if not m:
                return None
            day = pub.date() + dt.timedelta(days=1 if m.group(1) in ("あす", "明日") else 0)
            hh, mi = _pm(int(m.group(3)), m.group(1) + (m.group(2) or "")), int(m.group(4) or m.group(5) or 0)
            mo, da = day.month, day.day
    if not (1 <= mo <= 12 and 1 <= da <= 31 and 0 <= hh <= 29 and 0 <= mi <= 59):
        return None
    year = pub.year + (1 if mo < pub.month - 1 else 0)   # 12月の記事に「1月…」とあれば、次の年
    try:
        base = dt.datetime(year, mo, da)
    except ValueError:
        return None
    return base + dt.timedelta(hours=hh, minutes=mi)   # 「25時」のような深夜の書き方も、次の日の1時にする


MEMBERS = ("佐野勇斗", "吉田仁人", "塩﨑太智", "塩崎太智", "曽野舜太", "山中柔太朗")


def short_event_name(title):
    """記事の題名を、よてい帳に入れやすい短い名前にする。例：『溺愛 ～ 実は、最強ヒーロー ～』第4話（吉田仁人）"""
    t = re.split(r"[／/]", re.sub(r"^プレスリリース：", "", title))[0]
    m = re.search(r"([『「][^』」]{1,40}[』」])\s*(第\d+話)?", t)
    if m:
        name = m.group(1) + (m.group(2) or "")
        if "ドラマ" in t and "ドラマ" not in name:
            name = "ドラマ" + name
        who = [p for p in MEMBERS if p in t]
        return (name + (f"（{who[0]}）" if who else ""))[:60]
    return t[:60]


def guess_kind(title):
    if "Instagram" in title or "インスタ" in title or "TikTok" in title:
        return "sns"
    return ("drama" if "ドラマ" in title else "radio" if "ラジオ" in title
            else "youtube" if ("YouTube" in title or "配信" in title) else "tv")


def weekly_from_title(title, published, t):
    """「毎週土曜14:00〜」「毎週水曜21時から」を読み取る。 (つぎの日時, 曜日の数字) か None。"""
    m = ART_WEEKLY.search(title)
    if not m:
        return None
    try:   # 2か月より前の記事の「毎週」は、もう終わっているかもしれないので使わない
        pub = dt.datetime.fromisoformat(published).astimezone().replace(tzinfo=None)
        if pub < t - dt.timedelta(days=60):
            return None
    except (TypeError, ValueError):
        pass
    wd = "月火水木金土日".index(m.group(1))
    hh, mi = _pm(int(m.group(3)), m.group(2)), int(m.group(4) or m.group(5) or 0)
    if not (0 <= hh <= 29 and 0 <= mi <= 59):
        return None
    day = t.date() + dt.timedelta(days=(wd - t.weekday()) % 7)
    when = dt.datetime(day.year, day.month, day.day) + dt.timedelta(hours=hh, minutes=mi)
    if when <= t:
        when += dt.timedelta(weeks=1)
    return when


def auto_events_from_articles(d, t=None):
    """ニュース・ライブ・出演の記事から、これからの放送・配信の予定を読み取って、よてい帳に自動で入れる。入れた数を返す。"""
    t = t or now()
    added = 0
    for key, label in (("official", None), ("appear", None), ("live", None), ("sched", None), ("news", None)):
        for n in d.get(key, []):
            title = n.get("title", "")
            wk = weekly_from_title(title, n.get("published"), t)   # 毎週の番組・配信
            if wk:
                same = any(e.get("repeat") == "weekly" and e["time"] == wk.strftime("%H:%M")
                           and dt.date.fromisoformat(e["date"]).weekday() == wk.weekday() for e in d["events"])
                if not same and add_event(d, short_event_name(title), guess_kind(title), wk.strftime("%Y-%m-%d"),
                                          wk.strftime("%H:%M"), "weekly", n.get("url", ""), ["30", "start"],
                                          auto=True, src="wk:" + n.get("id", "")[:80]):
                    added += 1
                continue
            when = article_time(title, n.get("published"), t)
            if not when or not (t < when < t + dt.timedelta(days=60)):
                continue
            date, time_ = when.strftime("%Y-%m-%d"), when.strftime("%H:%M")
            if any(e["date"] == date and e["time"] == time_ for e in d["events"]):
                continue   # 同じ日時の予定は、もう入っている
            kind = guess_kind(title)
            name = short_event_name(title)
            if add_event(d, name, kind, date, time_, "none", n.get("url", ""), ["eve", "30", "start"],
                         auto=True, src="art:" + n.get("id", "")[:80]):
                added += 1
    return added


def public_events(d, t=None):
    """画面に出すよてい帳：つぎの予定の時刻をつけて、近い順にならべる。"""
    t = t or now()
    out = []
    for e in d["events"]:
        occ = next_occurrence(e, t - dt.timedelta(hours=3))   # 3時間前に始まったものまでは「きょう」に残す
        out.append({**e, "next": occ.strftime("%Y-%m-%dT%H:%M")})
    out.sort(key=lambda x: x["next"])
    return out


def send_deadline_reminders(d, t=None):
    """締め切りの 1週間前・3日前・前の日・当日の朝 に通知する。送った数を返す。"""
    t = t or now()
    if t.hour < REMIND_HOUR:
        return 0
    today = t.date()
    count = 0
    for dl in d["deadlines"]:
        try:
            due = dt.date.fromisoformat(dl["date"])
        except ValueError:
            continue
        left = (due - today).days
        if left in REMIND_DAYS and left not in dl.setdefault("reminded", []):
            dl["reminded"].append(left)
            when = "今日が締め切り！" if left == 0 else ("明日が締め切り" if left == 1 else f"あと{left}日で締め切り")
            notify(d, f"📅 {when}", f"{dl['name']}（{due.month}月{due.day}日まで）", dl.get("url") or None,
                   urgent=(left <= 1))
            count += 1
    return count


LAST_NEWS = [0.0]   # 最後にニュースを見に行った時刻（起動してから）


def tick(force_news=False):
    """見回り・まとめ通知・締め切り通知を、まとめて1回やる。"""
    with LOCK:
        seen = load()["seen"]
    # インターネットを見に行く間は、画面を待たせないように記録をにぎらない
    try:
        found, error = collect(seen), None
    except Exception as e:
        found, error = None, e
        print("見回りエラー:", e)
    news, news_error, live, appear, sched, video, official = None, None, None, None, None, None, None
    member_yt = {}
    # メンバー個人の YouTube：公式と同じ間かく＝3分ごと（2026-11-17 に10分→5分→3分。だめでも、ほかは続ける）
    for name, cid in MEMBER_CHANNELS.items():
        try:
            member_yt[name] = list(youtube_feed(cid))[:10]
        except Exception as e:
            print("メンバーのYouTubeのエラー:", name, e)
    news_time = force_news or time.time() - LAST_NEWS[0] >= NEWS_EVERY_SEC
    # 公式サイトの NEWS：1ページ目は3分ごと（YouTube といっしょ）、2ページ目はニュースといっしょに10分ごと（2026-11-18）
    try:
        official = official_feed(OFFICIAL_NEWS_PAGES if news_time else OFFICIAL_NEWS_PAGES[:1])
    except Exception as e:   # 公式サイトがだめでも、ほかは続ける
        print("公式サイトのエラー:", e)
    if news_time:
        LAST_NEWS[0] = time.time()
        try:
            news = news_feed()
        except Exception as e:   # ニュースがだめでも、YouTube の見回りは続ける
            news_error = e
            print("ニュースのエラー:", e)
        try:
            live = live_feed()
        except Exception as e:   # ライブ情報がだめでも、ほかは続ける
            print("ライブ情報のエラー:", e)
        try:
            appear = appear_feed()
        except Exception as e:   # 出演情報がだめでも、ほかは続ける
            print("出演情報のエラー:", e)
        try:
            video = []
            for n in news_feed(VIDEO_URL):   # 題名に動画・インタビューのことばがあるもの＋TVer・YouTube のページそのもの
                if (VIDEO_WORDS.search(n["title"]) or n["source"] in ("TVer", "YouTube")) and video_ok(n):
                    n["official"] = "オフィシャル" in n["source"]
                    video.append(n)
        except Exception as e:   # 動画の記事がだめでも、ほかは続ける
            video = None
            print("動画の記事のエラー:", e)
        try:
            sched = []
            for q in SCHED_QUERIES:
                url = ("https://news.google.com/rss/search?q=" + urllib.parse.quote(q) + "&hl=ja&gl=JP&ceid=JP:ja")
                sched += news_feed(url)
        except Exception as e:   # 予定さがしがだめでも、ほかは続ける
            print("予定さがしのエラー:", e)
    with LOCK:
        d = load()
        if error is None:
            apply_found(d, found)
        else:
            d["last_error"] = f"{now().strftime('%H:%M')} 見回りがうまくいきませんでした（インターネットにつながっているか確かめてください）"
        if news is not None:
            apply_news(d, news)
        elif news_error is not None:
            d["news_error"] = f"{now().strftime('%H:%M')} ニュースを集められませんでした"
        if live is not None:
            apply_live(d, live)
        if appear is not None:
            apply_topic(d, "appear", appear)
        if sched:
            apply_topic(d, "sched", sched)
        if official:
            apply_topic(d, "official", official)
        if video:
            apply_topic(d, "video", video)
        if member_yt:
            d["member_yt"] = {**d.get("member_yt", {}), **member_yt}
        if "video" in d:   # 前に入った古い TVer・こわれた題名も、一覧から外す
            d["video"] = [n for n in d["video"] if video_ok(n)]
        if news is not None or live is not None or appear is not None or sched or official:
            auto_events_from_articles(d)   # 記事にのっていた放送・配信の予定を、よてい帳に自動で入れる
        send_batch_if_time(d)
        send_deadline_reminders(d)
        send_event_reminders(d)
        save(d)
        return d


def loop():
    last = 0
    while True:
        try:   # 思わぬエラーがあっても、見回りを止めない（2026年11月14日 追加）
            if time.time() - last >= CHECK_EVERY_SEC:
                tick()
                last = time.time()
            else:
                with LOCK:   # 見回りの合間も、時間になったら通知だけは送る
                    d = load()
                    send_batch_if_time(d)
                    send_deadline_reminders(d)
                    send_event_reminders(d)
                    save(d)
        except Exception as e:
            print("見回りのとちゅうでエラー:", e)
            last = time.time()
        time.sleep(30)


# ---------------- 画面とのやりとり ----------------

def public_state(d):
    today = now().date()
    dls = []
    for x in d["deadlines"]:
        try:
            left = (dt.date.fromisoformat(x["date"]) - today).days
        except ValueError:
            left = None
        dls.append({**x, "left": left})
    dls.sort(key=lambda x: x["date"])
    return {"last_check": d["last_check"], "last_error": d["last_error"],
            "items": d["items"], "deadlines": dls,
            "pending": len(d["pending_batch"]), "dry_run": ARGS.dry_run,
            "notify_log": d["notify_log"][:5],
            "news": d["news"], "news_check": d["news_check"], "news_error": d["news_error"],
            "live": d["live"], "appear": d["appear"], "video": d["video"], "member_yt": d.get("member_yt", {}), "official": d.get("official", []), "events": public_events(d), "now": now().strftime("%Y-%m-%dT%H:%M")}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send_json(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        if self.path in ("/", "/app.html"):
            b = APP_HTML.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
        elif self.path == "/api/state":
            with LOCK:
                self.send_json(public_state(load()))
        else:
            self.send_json({"error": "見つかりません"}, 404)

    def do_POST(self):
        if self.path == "/api/check":
            self.send_json(public_state(tick(force_news=True)))
        elif self.path == "/api/deadlines":
            b = self.body()
            name = (b.get("name") or "").strip()
            date = (b.get("date") or "").strip()
            url = (b.get("url") or "").strip()
            try:
                dt.date.fromisoformat(date)
            except ValueError:
                return self.send_json({"error": "締め切りの日を入れてください"}, 400)
            if not name:
                return self.send_json({"error": "なにの締め切りかを入れてください"}, 400)
            with LOCK:
                d = load()
                d["deadlines"].append({"id": uuid.uuid4().hex[:8], "name": name[:100],
                                       "date": date, "url": url[:500], "reminded": []})
                send_deadline_reminders(d)   # 登録した日がちょうどお知らせの日なら、すぐ送る
                save(d)
                self.send_json(public_state(d))
        elif self.path == "/api/events":   # よてい帳に入れる
            b = self.body()
            name = (b.get("name") or "").strip()
            if not name:
                return self.send_json({"error": "なにを見るかを入れてください"}, 400)
            notify_keys = b.get("notify") if isinstance(b.get("notify"), list) else ["30", "start"]
            with LOCK:
                d = load()
                try:
                    added = add_event(d, name, b.get("kind") or "tv", (b.get("date") or "").strip(),
                                      (b.get("time") or "").strip(), b.get("repeat") or "none",
                                      (b.get("url") or "").strip(), notify_keys)
                except ValueError:
                    return self.send_json({"error": "日にちと時間を入れてください"}, 400)
                if not added:
                    return self.send_json({"error": "その予定は、もうよてい帳に入っています"}, 400)
                send_event_reminders(d)
                save(d)
                self.send_json(public_state(d))
        elif self.path == "/api/test-notify":
            with LOCK:
                d = load()
                ok = notify(d, "🥛 M!LKポスト テスト通知", "スマホに届いていたら成功です！")
                save(d)
                self.send_json({**public_state(d), "test_ok": ok})
        else:
            self.send_json({"error": "見つかりません"}, 404)

    def do_DELETE(self):
        m = re.fullmatch(r"/api/(deadlines|events)/([0-9a-f]+)", self.path)
        if not m:
            return self.send_json({"error": "見つかりません"}, 404)
        with LOCK:
            d = load()
            d[m.group(1)] = [x for x in d[m.group(1)] if x["id"] != m.group(2)]
            save(d)
            self.send_json(public_state(d))


def main():
    global ARGS
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--data", type=pathlib.Path, default=FOLDER / "data.json")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-browser", action="store_true")
    ARGS = ap.parse_args()

    try:
        server = ThreadingHTTPServer(("127.0.0.1", ARGS.port), Handler)
    except OSError:
        print("M!LKポストは、もう動いているようです。ブラウザで開きます。")
        if not ARGS.no_browser:
            webbrowser.open(f"http://127.0.0.1:{ARGS.port}/")
        return

    threading.Thread(target=loop, daemon=True).start()
    url = f"http://127.0.0.1:{ARGS.port}/"
    print("🥛 M!LKポスト 動いています。")
    print(f"   画面: {url}")
    print("   この窓を閉じると、見回りも止まります。")
    if not ARGS.no_browser:
        webbrowser.open(url)
    server.serve_forever()


if __name__ == "__main__":
    main()
