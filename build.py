# GitHub で自動で見回りをして、見るだけのページ（index.html）を作る（2026-11-22）
# GitHub Actions（.github/workflows/update.yml）が5分ごとに動かす。
# milkpost.py の見回り（YouTube・公式サイト・ニュース）をそのまま使う。通知は送らない（dry_run）。
# 入れないもの：よてい帳・しめきり帳・通知の記録・通知の設定
import argparse
import json
import pathlib

import milkpost as m

HERE = pathlib.Path(__file__).resolve().parent
DATA = HERE / "state.json"   # 前回までに集めたもの（次の見回りで使う）

m.ARGS = argparse.Namespace(dry_run=True, data=DATA, port=0, no_browser=True)
d = m.tick(force_news=True)
state = m.public_state(d)

# 自分だけのものは空にする
for k in ("deadlines", "events", "notify_log"):
    state[k] = []
state["pending"] = 0
state["dry_run"] = False
state["last_error"] = None
state["news_error"] = None

# state.json にも、よてい帳などは残さない
saved = json.loads(DATA.read_text(encoding="utf-8"))
for k in ("deadlines", "events", "notify_log", "pending_batch", "batch_sent"):
    saved[k] = [] if isinstance(saved.get(k), list) else saved.get(k)
DATA.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")

html = (HERE / "app.html").read_text(encoding="utf-8").replace("みんさん", "持ち主")
html = html.replace("<head>", '<head>\n<meta name="robots" content="noindex, nofollow">', 1)
hide = """  /* 公開用：見るだけのページなので、自分だけの場所とボタンはかくす */
  #evTitle, #cands, #evBox, #evs, #dlTitle, #form, #dls, .foot, #btnCheck, #checkMsg, #next { display: none !important; }
  .static-note { text-align: center; font-size: 13px; color: #8a6d5a; background: #fffdf6; border: 2px dashed #ffe36e; border-radius: 14px; padding: 6px 10px; margin: 8px 0; }
"""
html = html.replace("</style>", hide + "</style>", 1)
snap = state.get("last_check") or ""
note = f'<p class="static-note">🔄 {snap} の情報です（5分ごとくらいに自動で新しくなります）</p>'
html = html.replace('<div id="err"></div>', '<div id="err"></div>\n  ' + note, 1)
data = json.dumps(state, ensure_ascii=False).replace("</", "<\\/")
old_api = "async function api(path, opt = {}) {"
assert old_api in html
html = html.replace(old_api, f"const STATIC_STATE = {data};\nasync function api(path, opt = {{}}) {{ return JSON.parse(JSON.stringify(STATIC_STATE)); }}\nasync function api_off(path, opt = {{}}) {{", 1)
(HERE / "index.html").write_text(html, encoding="utf-8")
print("できました:", snap)
