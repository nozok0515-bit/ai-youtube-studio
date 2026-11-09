#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI YouTube Studio - 動画自動生成エンジン
企画 → 台本 → ナレーション → 図解スライド → 自動編集 → 書き出し → 投稿データ

使い方（GitHub Actions から自動で呼ばれます）:
  python generate.py plan     --theme "..." --format test --genre 雑学・科学
  python generate.py script
  python generate.py voice    --voice zundamon
  python generate.py visuals
  python generate.py edit     --quality draft
  python generate.py package
  python generate.py all  ...（上の全工程を1回で実行）

外部API:
  GEMINI_API_KEY があれば Gemini（無料枠）で企画・台本を生成。無ければ内蔵台本/テンプレート。
  ナレーション: VOICEVOX ENGINE（Actionsのサービスコンテナ）→ edge-tts → gTTS の順に自動切替。
"""
import argparse, glob, json, math, os, random, re, shutil, subprocess, sys, time, wave
import urllib.request, urllib.parse, urllib.error
from datetime import datetime, timezone, timedelta

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

WORK = os.environ.get("WORK_DIR", "work")
OUT = os.environ.get("OUT_DIR", "output")
STATE = os.path.join(WORK, "state.json")
JST = timezone(timedelta(hours=9))
SR = 44100

# ---------------------------------------------------------------- 設定
FORMATS = {
    "test":     {"label": "テスト（約25秒・縦型）", "vertical": True,  "seconds": 25},
    "shorts60": {"label": "Shorts（約60秒・縦型）", "vertical": True,  "seconds": 58},
    "normal3":  {"label": "通常動画（約3分・横型）", "vertical": False, "seconds": 180},
    "normal8":  {"label": "通常動画（約8分・横型）", "vertical": False, "seconds": 480},
}
QUALITY = {  # draft = 半分の解像度で高速テスト / hd = 本番
    "draft": {"scale": 0.5, "fps": 24, "crf": 26},
    "hd":    {"scale": 1.0, "fps": 30, "crf": 20},
}
VOICES = {  # VOICEVOX speaker id とクレジット表記
    "zundamon": {"id": 3,  "credit": "VOICEVOX:ずんだもん", "edge": "ja-JP-NanamiNeural"},
    "metan":    {"id": 2,  "credit": "VOICEVOX:四国めたん", "edge": "ja-JP-NanamiNeural"},
    "tsumugi":  {"id": 8,  "credit": "VOICEVOX:春日部つむぎ", "edge": "ja-JP-NanamiNeural"},
    "ryusei":   {"id": 13, "credit": "VOICEVOX:青山龍星", "edge": "ja-JP-KeitaNeural"},
}
GENRES = {
    "雑学・科学":   {"bg": ((14, 30, 64), (36, 82, 160)),  "accent": (255, 204, 64)},
    "お金・節約":   {"bg": ((10, 48, 38), (24, 120, 88)),  "accent": (255, 220, 120)},
    "テクノロジー": {"bg": ((20, 16, 48), (72, 48, 168)),  "accent": (96, 230, 255)},
    "歴史":         {"bg": ((48, 28, 16), (128, 72, 36)),  "accent": (255, 214, 150)},
    "心理学・習慣": {"bg": ((52, 18, 48), (150, 52, 110)), "accent": (255, 190, 220)},
    "健康・生活":   {"bg": ((12, 52, 60), (30, 130, 140)), "accent": (200, 255, 210)},
}
TOPIC_IDEAS = {
    "雑学・科学": ["空が青いのはなぜ？", "雷はなぜジグザグに光る？", "氷が水に浮く不思議", "月はなぜいつも同じ面を向ける？"],
    "お金・節約": ["固定費を見直す3つの順番", "複利の仕組みをざっくり理解", "ポイント還元の落とし穴"],
    "テクノロジー": ["生成AIはどうやって文章を作る？", "パスワードより安全なパスキーとは", "Wi-Fiが遅くなる意外な原因"],
    "歴史": ["江戸時代の「時刻」の数え方", "なぜ1週間は7日なのか", "郵便番号が生まれた理由"],
    "心理学・習慣": ["三日坊主を防ぐ「if-thenプランニング」", "なぜ締め切り直前に集中できる？", "選択肢が多いと選べなくなる理由"],
    "健康・生活": ["睡眠の質を上げる夜の習慣", "正しい手洗いのポイント", "水分補給のタイミング"],
}

# 内蔵サンプル台本（APIキー無しでもテスト動画が意味のある内容になるように）
BUILTIN_SCRIPT = {
    "title": "空が青いのはなぜ？30秒でわかる光の秘密",
    "theme": "空が青いのはなぜ？",
    "scenes": [
        {"section": "hook", "heading": "空に色はない？", "narration": "毎日見ている青い空。実は、空気そのものが青く塗られているわけではありません。",
         "visual": {"type": "title", "big": "空が青いのはなぜ？", "sub": "30秒でわかる光の秘密"}},
        {"section": "body", "heading": "太陽の光は七色", "narration": "太陽の白い光には、虹の七色がすべて混ざっています。",
         "visual": {"type": "list", "items": ["太陽光＝いろいろな色の混合", "色ごとに「波長」が違う", "青は短く、赤は長い"]}},
        {"section": "body", "heading": "レイリー散乱", "narration": "光が空気の分子にぶつかると、波長の短い青い光ほど強く散らばります。これをレイリー散乱と呼びます。",
         "visual": {"type": "compare", "left": {"title": "赤い光", "text": "波長が長い\n散らばりにくい"}, "right": {"title": "青い光", "text": "波長が短い\nよく散らばる"}}},
        {"section": "body", "heading": "だから青く見える", "narration": "散らばった青い光が空のあらゆる方向から目に届くので、空は青く見えるのです。",
         "visual": {"type": "keyword", "big": "四方八方から\n青い光が届く", "sub": "POINT"}},
        {"section": "outro", "heading": "夕焼けが赤い理由", "narration": "ちなみに夕焼けが赤いのは、光が長い距離を通るうちに青い光が散らばり、赤い光が残るからです。",
         "visual": {"type": "keyword", "big": "夕焼け＝\n赤い光が残る", "sub": "おまけ"}},
    ],
    "description": "空が青く見える理由を「レイリー散乱」から30秒で解説します。",
    "hashtags": ["雑学", "科学", "空", "レイリー散乱", "Shorts"],
}


# ---------------------------------------------------------------- 共通
class StepError(Exception):
    def __init__(self, msg, hint=""):
        super().__init__(msg)
        self.hint = hint


def log(msg):
    print(msg, flush=True)


def load_state():
    if not os.path.exists(STATE):
        raise StepError("作業データが見つかりません。", "最初から「Run workflow」をやり直してください。")
    with open(STATE, encoding="utf-8") as f:
        return json.load(f)


def save_state(st):
    os.makedirs(WORK, exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)


def run(cmd, what):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        tail = "\n".join(p.stderr.strip().splitlines()[-12:])
        raise StepError(f"{what}に失敗しました。\n{tail}", "もう一度実行しても直らない場合は、画質を「draft」にして試してください。")
    return p


def summary(md):
    """GitHub Actions の実行画面に表示するメモ"""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(md + "\n")


def find_font(bold=True):
    names = ["NotoSansCJK-Bold.ttc", "NotoSansCJKjp-Bold.otf"] if bold else ["NotoSansCJK-Medium.ttc", "NotoSansCJK-Regular.ttc", "NotoSansCJKjp-Regular.otf"]
    for n in names:
        hits = glob.glob(f"/usr/share/fonts/**/{n}", recursive=True)
        if hits:
            return hits[0]
    hits = glob.glob("/usr/share/fonts/**/*CJK*", recursive=True)
    if hits:
        return hits[0]
    raise StepError("日本語フォントが見つかりません。", "ワークフローの「準備」工程が失敗していないか確認してください。")


_FONT_CACHE = {}


def font(size, bold=True):
    key = (size, bold)
    if key not in _FONT_CACHE:
        _FONT_CACHE[key] = ImageFont.truetype(find_font(bold), size, index=0)
    return _FONT_CACHE[key]


# ---------------------------------------------------------------- STEP1 企画 / STEP2 台本（Gemini）
GEMINI_MODELS = [m for m in [os.environ.get("GEMINI_MODEL", "").strip(), "gemini-flash-latest", "gemini-flash-lite-latest", "gemini-2.5-flash"] if m]


def gemini(prompt, want_json=True):
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        return None
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.8}}
    if want_json:
        body["generationConfig"]["responseMimeType"] = "application/json"
    last = ""
    for model in GEMINI_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "x-goog-api-key": key})
        for attempt in range(2):
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    data = json.loads(r.read().decode())
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                log(f"  Gemini({model}) OK")
                return json.loads(_strip_json(text)) if want_json else text
            except urllib.error.HTTPError as e:
                last = f"{model}: HTTP {e.code} {e.read()[:200]!r}"
                if e.code == 429:
                    time.sleep(5)
                    continue
                break
            except Exception as e:  # JSON崩れなど
                last = f"{model}: {e}"
                continue
        log(f"  Gemini失敗 → 次を試す ({last})")
    log("  Gemini が使えなかったため内蔵テンプレートに切り替えます: " + last)
    return None


def _strip_json(t):
    t = t.strip()
    t = re.sub(r"^```(json)?", "", t).strip()
    t = re.sub(r"```$", "", t).strip()
    return t


def step_plan(a):
    fmt = FORMATS[a.format]
    genre = a.genre if a.genre in GENRES else "雑学・科学"
    theme = (a.theme or "").strip()
    source = "ユーザー指定"
    if not theme:
        res = gemini(
            f"あなたは日本のYouTube企画担当です。ジャンル「{genre}」で、{fmt['label']}の動画テーマを1つ提案してください。"
            "条件: 検索需要がある素朴な疑問や実用テーマ／事実で説明できる／誤解を招かない／他人の作品やニュース記事の転載に依存しない／医療・投資の断定的助言を含まない。"
            '出力はJSONのみ: {"theme": "テーマ（30字以内）", "reason": "選んだ理由（60字以内）"}')
        if res and res.get("theme"):
            theme, source = res["theme"].strip(), "Gemini提案: " + res.get("reason", "")
        else:
            theme = BUILTIN_SCRIPT["theme"] if a.format == "test" and genre == "雑学・科学" else random.choice(TOPIC_IDEAS[genre])
            source = "内蔵ネタ帳から選択"
    st = {"theme": theme, "theme_source": source, "format": a.format, "genre": genre,
          "voice": a.voice, "quality": a.quality, "created": datetime.now(JST).strftime("%Y-%m-%d %H:%M")}
    save_state(st)
    log(f"テーマ: {theme}（{source}）")
    summary(f"### STEP1 企画\n- テーマ: **{theme}**\n- 決定方法: {source}\n- 形式: {fmt['label']} / ジャンル: {genre}")


def step_script(a):
    st = load_state()
    fmt = FORMATS[st["format"]]
    secs = fmt["seconds"]
    chars = int(secs * 6.3)  # 日本語ナレーション ≒ 1秒あたり6〜7文字
    n_scenes = max(4, min(40, round(secs / 9)))
    script, source = None, ""
    if os.environ.get("GEMINI_API_KEY"):
        prompt = f"""あなたは日本語の解説系YouTube動画の構成作家です。次のテーマで、{fmt['label']}用のオリジナル台本を作ってください。
テーマ: {st['theme']}
ジャンル: {st['genre']}
条件:
- ナレーション合計 約{chars}文字、シーン数 {n_scenes}前後（1シーン20〜70文字）
- 1シーン目は視聴者の興味を引くフック（section="hook"）、最後は結論とまとめ（section="outro"）、その他は section="body"
- 事実に基づき、断定できないことは断定しない。誇張・釣り・誤情報は禁止。医療や投資は一般論にとどめる
- 特定の作品・記事の文章を引用/転載しない。自分の言葉で、具体例や比較を入れて独自性を出す
- 各シーンに画面用の図解 visual を付ける。typeは次のどれか:
  "title"(big:タイトル, sub:サブ), "keyword"(big:強調語句 改行は\\n, sub:小見出し), "list"(items:2〜4個の短文),
  "compare"(left:{{title,text}}, right:{{title,text}}), "number"(big:数字や短い値, sub:説明)
- 図解の文字は短く（1項目16文字以内）
出力はJSONのみ:
{{"title":"YouTubeタイトル(40字以内、誇張なし)","description":"概要(150字程度)","hashtags":["5個"],
 "scenes":[{{"section":"hook","heading":"見出し","narration":"読み上げ文","visual":{{...}}}}]}}"""
        script = gemini(prompt)
        source = "Gemini（無料枠）で生成"
    if not script or not script.get("scenes"):
        if st["format"] == "test" and st["theme"] == BUILTIN_SCRIPT["theme"]:
            script, source = json.loads(json.dumps(BUILTIN_SCRIPT)), "内蔵サンプル台本（事実確認済みの例文）"
        else:
            script, source = template_script(st["theme"], n_scenes), "テンプレート台本（Gemini未設定のため簡易版）"
    script = normalize_script(script, st["theme"])
    st["script"], st["script_source"] = script, source
    save_state(st)
    total = sum(len(s["narration"]) for s in script["scenes"])
    log(f"台本: {len(script['scenes'])}シーン / {total}文字 / {source}")
    summary(f"### STEP2 台本\n- {source}\n- {len(script['scenes'])}シーン・約{total}文字\n- タイトル案: {script['title']}")


def template_script(theme, n):
    """APIなしの簡易台本。内容は一般的なので本番投稿前に Gemini 設定を推奨。"""
    body = max(2, n - 2)
    scenes = [{"section": "hook", "heading": theme, "narration": f"今日のテーマは「{theme}」。知っているようで説明できない、このテーマを短く整理します。",
               "visual": {"type": "title", "big": theme, "sub": "わかりやすく解説"}}]
    for i in range(1, body + 1):
        scenes.append({"section": "body", "heading": f"ポイント{i}",
                       "narration": f"ポイント{i}。ここに「{theme}」の具体的な説明が入ります。Gemini APIキーを設定すると、実際の解説文が自動で作られます。",
                       "visual": {"type": "number", "big": f"POINT {i}", "sub": "Gemini設定で本文を自動作成"}})
    scenes.append({"section": "outro", "heading": "まとめ", "narration": "以上、要点のまとめでした。最後まで見ていただきありがとうございました。",
                   "visual": {"type": "keyword", "big": "まとめ", "sub": theme}})
    return {"title": theme, "description": f"{theme}について解説します。", "hashtags": ["解説"], "scenes": scenes}


def normalize_script(sc, theme):
    out = {"title": str(sc.get("title") or theme)[:70], "description": str(sc.get("description") or ""),
           "hashtags": [str(h).lstrip("#").replace(" ", "") for h in (sc.get("hashtags") or [])][:8], "scenes": []}
    for i, s in enumerate(sc.get("scenes", [])):
        nar = re.sub(r"\s+", " ", str(s.get("narration", ""))).strip()
        if not nar:
            continue
        v = s.get("visual") or {}
        if v.get("type") not in ("title", "keyword", "list", "compare", "number"):
            v = {"type": "keyword", "big": s.get("heading") or nar[:14], "sub": ""}
        out["scenes"].append({"section": s.get("section", "body"), "heading": str(s.get("heading", ""))[:30], "narration": nar, "visual": v})
    if not out["scenes"]:
        raise StepError("台本が空でした。", "もう一度実行してください。続く場合はテーマを具体的にしてください。")
    out["scenes"][0]["section"] = "hook"
    return out


# ---------------------------------------------------------------- STEP3 ナレーション
def tts_voicevox(text, speaker, path):
    base = os.environ.get("VOICEVOX_URL", "http://127.0.0.1:50021")
    q = urllib.parse.urlencode({"text": text, "speaker": speaker})
    with urllib.request.urlopen(urllib.request.Request(f"{base}/audio_query?{q}", method="POST"), timeout=60) as r:
        query = json.loads(r.read())
    query["speedScale"] = 1.1
    query["prePhonemeLength"] = 0.05
    query["postPhonemeLength"] = 0.1
    req = urllib.request.Request(f"{base}/synthesis?speaker={speaker}", data=json.dumps(query).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=120) as r:
        open(path, "wb").write(r.read())


def voicevox_ready(wait=0):
    base = os.environ.get("VOICEVOX_URL", "http://127.0.0.1:50021")
    end = time.time() + wait
    while True:
        try:
            with urllib.request.urlopen(f"{base}/version", timeout=5) as r:
                return r.read().decode().strip('"')
        except Exception:
            if time.time() > end:
                return None
            time.sleep(3)


def tts_edge(text, voice, path):
    import asyncio, edge_tts
    asyncio.run(edge_tts.Communicate(text, voice, rate="+8%").save(path))


def tts_gtts(text, path):
    from gtts import gTTS
    gTTS(text, lang="ja").save(path)


def tts_dummy(text, path):  # ローカル検証用（音声エンジンが無い環境）
    dur = len(text) / 7.0
    t = np.linspace(0, dur, int(SR * dur), endpoint=False)
    sig = 0.15 * np.sin(2 * np.pi * 220 * t) * (np.sin(2 * np.pi * 3 * t) > 0)
    write_wav(path, sig)


def to_wav(src, dst):
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-ac", "1", "-ar", str(SR), "-sample_fmt", "s16", dst], "音声変換")


def read_wav(path):
    with wave.open(path) as w:
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    return data


def write_wav(path, sig, ch=1):
    sig = np.clip(sig, -1, 1)
    with wave.open(path, "wb") as w:
        w.setnchannels(ch)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((sig * 32767).astype(np.int16).tobytes())


def step_voice(a):
    st = load_state()
    vkey = a.voice or st.get("voice") or "zundamon"
    vinfo = VOICES.get(vkey, VOICES["zundamon"])
    forced = os.environ.get("TTS_ENGINE", "").strip()
    engines = [forced] if forced else ["voicevox", "edge", "gtts"]
    if "voicevox" in engines:
        ver = voicevox_ready(wait=int(os.environ.get("VOICEVOX_WAIT", "150")))
        if ver:
            log(f"VOICEVOX ENGINE {ver} に接続しました")
        else:
            log("VOICEVOX に接続できないため、別の音声エンジンに切り替えます")
            engines = [e for e in engines if e != "voicevox"] or ["edge"]
    adir = os.path.join(WORK, "audio")
    os.makedirs(adir, exist_ok=True)
    used = None
    for i, sc in enumerate(st["script"]["scenes"]):
        text = sc["narration"]
        ok = False
        for eng in ([used] if used else []) + [e for e in engines if e != used]:
            raw = os.path.join(adir, f"raw{i:03d}" + (".wav" if eng in ("voicevox", "dummy") else ".mp3"))
            try:
                if eng == "voicevox":
                    tts_voicevox(text, vinfo["id"], raw)
                elif eng == "edge":
                    tts_edge(text, vinfo["edge"], raw)
                elif eng == "gtts":
                    tts_gtts(text, raw)
                elif eng == "dummy":
                    tts_dummy(text, raw)
                else:
                    continue
                wav = os.path.join(adir, f"s{i:03d}.wav")
                to_wav(raw, wav)
                sc["audio"] = wav
                sc["voice_sec"] = round(len(read_wav(wav)) / SR, 3)
                used, ok = eng, True
                break
            except Exception as e:
                log(f"  シーン{i+1}: {eng} 失敗 ({e})")
        if not ok:
            raise StepError("ナレーション音声を作れませんでした（全ての音声エンジンが失敗）。",
                            "数分おいてから再実行してください。VOICEVOXが起動に時間がかかっている可能性があります。")
        log(f"  シーン{i+1}/{len(st['script']['scenes'])}: {sc['voice_sec']}秒 ({used})")
    credit = {"voicevox": vinfo["credit"], "edge": "音声: Microsoft Edge 読み上げ (edge-tts経由)",
              "gtts": "音声: Google翻訳読み上げ (gTTS経由)", "dummy": "音声: テスト用ダミー"}[used]
    st["tts"] = {"engine": used, "voice": vkey, "credit": credit}
    save_state(st)
    total = sum(s["voice_sec"] for s in st["script"]["scenes"])
    summary(f"### STEP3 ナレーション\n- エンジン: **{used}** / {credit}\n- 合計 {total:.1f}秒")
    if used != "voicevox":
        summary("> ⚠️ VOICEVOX以外の音声で作成しました。この音声は商用利用の許諾が明確ではないため、**収益化する動画には使わず**、再実行してVOICEVOXで作り直すことをおすすめします。")


# ---------------------------------------------------------------- STEP4 図解スライド
def dims(st, quality=None):
    vert = FORMATS[st["format"]]["vertical"]
    W, H = (1080, 1920) if vert else (1920, 1080)
    s = QUALITY[quality or st.get("quality", "draft")]["scale"]
    return W, H, int(W * s) // 2 * 2, int(H * s) // 2 * 2


def wrap(text, fnt, maxw):
    lines = []
    for para in str(text).split("\n"):
        cur = ""
        for ch in para:
            if fnt.getlength(cur + ch) > maxw and cur:
                if ch in "、。！？」』）,.!?ー":  # 行頭禁則
                    cur += ch
                    continue
                lines.append(cur)
                cur = ch
            else:
                cur += ch
        lines.append(cur)
    return [l for l in lines if l != ""] or [""]


def balanced_wrap(text, fnt, maxw):
    """行の長さをそろえる（「空が青いのはな／ぜ？」のような泣き別れを防ぐ）"""
    lines = wrap(text, fnt, maxw)
    if len(lines) < 2 or "\n" in str(text):
        return lines
    target = fnt.getlength(text) / len(lines) * 1.08
    alt = wrap(text, fnt, max(target, fnt.size * 3))
    return alt if len(alt) == len(lines) else lines


def fit_text(text, maxw, maxh, start, minsize=28, spacing=1.25):
    size = start
    while size > minsize:
        f = font(size)
        lines = balanced_wrap(text, f, maxw)
        orphan = len(lines) > 1 and len(lines[-1]) <= 2
        if len(lines) * size * spacing <= maxh and not orphan:
            return f, lines, size
        size -= 4
    f = font(minsize)
    return f, wrap(text, f, maxw), minsize


def draw_lines(d, lines, f, size, cx, top, fill, spacing=1.25, align="center", left=0):
    y = top
    for l in lines:
        if align == "center":
            d.text((cx, y), l, font=f, fill=fill, anchor="ma")
        else:
            d.text((left, y), l, font=f, fill=fill)
        y += size * spacing
    return y


def gradient_bg(W, H, c1, c2, seed):
    y = np.linspace(0, 1, H)[:, None]
    x = np.linspace(0, 1, W)[None, :]
    t = np.clip(0.65 * y + 0.35 * x, 0, 1)[..., None]
    arr = (np.array(c1) * (1 - t) + np.array(c2) * t).astype(np.uint8)
    img = Image.fromarray(arr, "RGB")
    # オリジナルの幾何学模様（円とライン）
    rnd = random.Random(seed)
    over = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(over)
    for _ in range(7):
        r = rnd.randint(min(W, H) // 8, min(W, H) // 2)
        x0, y0 = rnd.randint(-r, W), rnd.randint(-r, H)
        od.ellipse([x0, y0, x0 + r, y0 + r], outline=(255, 255, 255, rnd.randint(18, 40)), width=rnd.randint(2, 6))
    for i in range(0, W + H, 90):
        od.line([(i, 0), (i - H, H)], fill=(255, 255, 255, 8), width=2)
    over = over.filter(ImageFilter.GaussianBlur(1))
    return Image.alpha_composite(img.convert("RGBA"), over)


def rounded(d, box, r, fill, outline=None, width=0):
    d.rounded_rectangle(box, r, fill=fill, outline=outline, width=width)


def render_slide(st, i, sc, W, H):
    g = GENRES[st["genre"]]
    acc = g["accent"]
    vert = H > W
    img = gradient_bg(W, H, *g["bg"], seed=i * 7 + 3)
    d = ImageDraw.Draw(img)
    n = len(st["script"]["scenes"])
    m = int(W * 0.07)
    # 上部: 進行バー + セクションラベル
    bar_y = int(H * (0.06 if vert else 0.07))
    rounded(d, [m, bar_y, W - m, bar_y + 10], 5, (255, 255, 255, 50))
    rounded(d, [m, bar_y, m + int((W - 2 * m) * (i + 1) / n), bar_y + 10], 5, acc + (255,))
    label = {"hook": "INTRO", "outro": "まとめ"}.get(sc["section"], f"POINT {i}")
    lf = font(int(W * (0.035 if vert else 0.022)))
    lw = lf.getlength(label)
    rounded(d, [m, bar_y + 34, m + lw + 48, bar_y + 34 + lf.size + 28], (lf.size + 28) // 2, acc + (255,))
    d.text((m + 24, bar_y + 34 + 14), label, font=lf, fill=(20, 20, 30))
    if sc.get("heading") and sc["section"] != "hook":
        hf = font(int(W * (0.042 if vert else 0.026)))
        d.text((m + lw + 72, bar_y + 34 + (lf.size + 28) // 2), sc["heading"], font=hf, fill=(255, 255, 255), anchor="lm")
    # 中央: 図解カード（下部は字幕エリアとして空ける）
    top = int(H * (0.17 if vert else 0.2))
    bottom = int(H * (0.70 if vert else 0.69))
    v = sc["visual"]
    t = v.get("type")
    cw = W - 2 * m
    white, dark = (255, 255, 255), (24, 26, 40)
    if t == "title":
        f, lines, sz = fit_text(v.get("big", ""), cw, (bottom - top) * 0.6, int(W * (0.11 if vert else 0.07)))
        tot = len(lines) * sz * 1.25
        y = top + ((bottom - top) - tot) / 2 - sz * 0.6
        # 影付き大見出し
        for dx, dy in ((4, 6),):
            draw_lines(d, lines, f, sz, W / 2 + dx, y + dy, (0, 0, 0, 110))
        y2 = draw_lines(d, lines, f, sz, W / 2, y, white)
        if v.get("sub"):
            sf = font(int(sz * 0.42))
            sw = sf.getlength(v["sub"])
            rounded(d, [W / 2 - sw / 2 - 30, y2 + 40, W / 2 + sw / 2 + 30, y2 + 40 + sf.size + 30], 16, acc + (255,))
            d.text((W / 2, y2 + 55), v["sub"], font=sf, fill=dark, anchor="ma")
    elif t == "keyword" or t == "number":
        rounded(d, [m, top, W - m, bottom], 40, (255, 255, 255, 235))
        pad = int(W * 0.05)
        if v.get("sub"):
            sf = font(int(W * (0.04 if vert else 0.025)))
            d.text((W / 2, top + pad), v["sub"], font=sf, fill=tuple(int(c * 0.6) for c in g["bg"][1]), anchor="ma")
        start = int(W * (0.16 if t == "number" else 0.1) * (1 if vert else 0.62))
        f, lines, sz = fit_text(v.get("big", ""), cw - 2 * pad, (bottom - top) - 3 * pad, start)
        tot = len(lines) * sz * 1.25
        y = top + ((bottom - top) - tot) / 2 + pad * 0.3
        draw_lines(d, lines, f, sz, W / 2, y, dark)
        d.rectangle([W / 2 - 60, bottom - pad, W / 2 + 60, bottom - pad + 10], fill=acc)
    elif t == "list":
        items = [str(x) for x in (v.get("items") or [])][:4] or [sc["heading"]]
        gap = int(H * 0.02)
        h_each = ((bottom - top) - gap * (len(items) - 1)) / len(items)
        for k, it in enumerate(items):
            y0 = top + k * (h_each + gap)
            rounded(d, [m, y0, W - m, y0 + h_each], 30, (255, 255, 255, 235))
            cs = int(min(h_each * 0.55, W * 0.11))
            cx0 = m + int(W * 0.04)
            d.ellipse([cx0, y0 + h_each / 2 - cs / 2, cx0 + cs, y0 + h_each / 2 + cs / 2], fill=acc)
            d.text((cx0 + cs / 2, y0 + h_each / 2), str(k + 1), font=font(int(cs * 0.6)), fill=dark, anchor="mm")
            tx = cx0 + cs + int(W * 0.035)
            f, lines, sz = fit_text(it, W - m - tx - int(W * 0.03), h_each * 0.8, int(W * (0.06 if vert else 0.035)))
            tot = len(lines) * sz * 1.2
            draw_lines(d, lines, f, sz, 0, y0 + (h_each - tot) / 2, dark, 1.2, align="left", left=tx)
    elif t == "compare":
        L, R = v.get("left") or {}, v.get("right") or {}
        if vert:
            boxes = [[m, top, W - m, top + (bottom - top) * 0.46], [m, bottom - (bottom - top) * 0.46, W - m, bottom]]
        else:
            boxes = [[m, top, W / 2 - m * 0.6, bottom], [W / 2 + m * 0.6, top, W - m, bottom]]
        for side, box, col in ((L, boxes[0], (255, 255, 255, 235)), (R, boxes[1], acc + (255,))):
            rounded(d, box, 34, col)
            bw, bh = box[2] - box[0], box[3] - box[1]
            tf, tl, tsz = fit_text(side.get("title", ""), bw * 0.9, bh * 0.3, int(W * (0.075 if vert else 0.045)))
            ytop = draw_lines(d, tl, tf, tsz, (box[0] + box[2]) / 2, box[1] + bh * 0.1, dark)
            bf, bl, bsz = fit_text(side.get("text", ""), bw * 0.85, box[3] - ytop - bh * 0.1, int(W * (0.05 if vert else 0.03)))
            draw_lines(d, bl, bf, bsz, (box[0] + box[2]) / 2, ytop + bh * 0.05, (60, 64, 80))
        vs = int(W * (0.09 if vert else 0.05))
        cx, cy = W / 2, (boxes[0][3] + boxes[1][1]) / 2 if vert else (top + bottom) / 2
        d.ellipse([cx - vs, cy - vs, cx + vs, cy + vs], fill=dark, outline=white, width=6)
        d.text((cx, cy), "VS", font=font(int(vs * 0.8)), fill=white, anchor="mm")
    # 終了カード
    if sc["section"] == "outro" and i == n - 1:
        ef = font(int(W * (0.038 if vert else 0.024)))
        col = g["bg"][1] if t in ("keyword", "number", "list") else acc
        d.text((W / 2, bottom - int(H * (0.065 if vert else 0.09))), "最後まで見てくれてありがとう！", font=ef, fill=col, anchor="ma")
    return img.convert("RGB")


def chunk_subs(text, maxchars):
    parts = re.split(r"(?<=[。！？!?])", text)
    chunks = []
    for p in parts:
        p = p.strip()
        while len(p) > maxchars:
            cut = max(p.rfind("、", 0, maxchars + 1), p.rfind(" ", 0, maxchars + 1))
            cut = cut + 1 if cut > maxchars // 3 else maxchars
            chunks.append(p[:cut])
            p = p[cut:].strip()
        if p:
            chunks.append(p)
    return chunks or [text]


def render_sub(text, W, H):
    vert = H > W
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    m = int(W * 0.06)
    sz = int(W * (0.058 if vert else 0.036))
    f = font(sz)
    lines = balanced_wrap(text, f, W - 2 * m - 40)
    lh = sz * 1.3
    boxh = len(lines) * lh + sz * 0.8
    y0 = H * 0.735 if vert else H * 0.96 - boxh
    rounded(d, [m, y0, W - m, y0 + boxh], 26, (0, 0, 0, 170))
    y = y0 + sz * 0.4
    for l in lines:
        d.text((W / 2, y), l, font=f, fill=(255, 255, 255), anchor="ma", stroke_width=2, stroke_fill=(0, 0, 0))
        y += lh
    return img


def step_visuals(a):
    st = load_state()
    W, H, _, _ = dims(st)
    vdir = os.path.join(WORK, "slides")
    os.makedirs(vdir, exist_ok=True)
    maxc = 32 if H > W else 34
    for i, sc in enumerate(st["script"]["scenes"]):
        p = os.path.join(vdir, f"slide{i:03d}.png")
        render_slide(st, i, sc, W, H).save(p)
        sc["slide"] = p
        subs = []
        for k, ch in enumerate(chunk_subs(sc["narration"], maxc)):
            sp = os.path.join(vdir, f"sub{i:03d}_{k:02d}.png")
            render_sub(ch, W, H).save(sp)
            subs.append({"text": ch, "png": sp})
        sc["subs"] = subs
        log(f"  スライド {i+1}/{len(st['script']['scenes'])}: {sc['visual'].get('type')}")
    save_state(st)
    summary(f"### STEP4 映像素材\n- オリジナル図解スライド {len(st['script']['scenes'])}枚（アプリ内で描画・外部素材なし）")


# ---------------------------------------------------------------- STEP5 編集 / STEP6 書き出し
def make_bgm(path, seconds):
    """アプリ内で合成するオリジナルBGM（著作権フリー）"""
    chords = [[220.0, 277.18, 329.63], [196.0, 246.94, 293.66], [174.61, 220.0, 261.63], [196.0, 246.94, 329.63]]
    n = int(SR * seconds)
    t = np.arange(n) / SR
    out = np.zeros(n, dtype=np.float32)
    seg = 4.0
    for j in range(int(math.ceil(seconds / seg))):
        a, b = int(j * seg * SR), min(n, int((j + 1) * seg * SR + SR))
        tt = t[a:b] - j * seg
        env = np.minimum(1, tt / 1.2) * np.exp(-np.maximum(0, tt - seg) * 3)
        for f in chords[j % 4]:
            out[a:b] += (np.sin(2 * np.pi * f * tt) + 0.3 * np.sin(2 * np.pi * f * 2 * tt)) * env * 0.08
    # やわらかいアルペジオ
    notes = [440, 554.37, 659.25, 554.37]
    step = 0.5
    for k in range(int(seconds / step)):
        a = int(k * step * SR)
        b = min(n, a + int(SR * 0.45))
        tt = t[a:b] - k * step
        out[a:b] += np.sin(2 * np.pi * notes[k % 4] * tt) * np.exp(-tt * 6) * 0.05
    fade = int(SR * 1.5)
    out[:fade] *= np.linspace(0, 1, fade)
    out[-fade:] *= np.linspace(1, 0, fade)
    write_wav(path, out / max(1e-6, np.abs(out).max()) * 0.6)


def step_edit(a):
    st = load_state()
    if a.quality:
        st["quality"] = a.quality
    q = QUALITY[st["quality"]]
    W, H, w, h = dims(st)
    fps = q["fps"]
    edir = os.path.join(WORK, "clips")
    shutil.rmtree(edir, ignore_errors=True)
    os.makedirs(edir)
    scenes = st["script"]["scenes"]
    narr, t_cursor, srt_items, chapters = [], 0.0, [], []
    lead, tail = 0.15, 0.35
    t0 = time.time()
    for i, sc in enumerate(scenes):
        vsec = sc["voice_sec"]
        frames = int(math.ceil((lead + vsec + tail) * fps))
        D = frames / fps
        # 音声: 前後に無音を足してフレーム長にぴったり合わせる
        voice = read_wav(sc["audio"])
        buf = np.zeros(int(round(D * SR)), dtype=np.float32)
        s0 = int(lead * SR)
        buf[s0:s0 + len(voice)] = voice[: len(buf) - s0]
        narr.append(buf)
        # 字幕タイミング（文字数比例）
        subs = sc["subs"]
        total = sum(len(x["text"]) for x in subs) or 1
        cur = lead
        for x in subs:
            dur = vsec * len(x["text"]) / total
            x["start"], x["end"] = cur, cur + dur
            srt_items.append((t_cursor + cur, t_cursor + cur + dur, x["text"]))
            cur += dur
        if sc["section"] != "body" or sc.get("heading"):
            chapters.append((t_cursor, sc.get("heading") or ("イントロ" if sc["section"] == "hook" else "まとめ")))
        # 映像: ゆっくりズーム（奇数シーンはズームアウト）+ 字幕重ね + フェード
        zin = i % 2 == 0
        zexpr = f"1+0.06*on/{frames}" if zin else f"1.06-0.06*on/{frames}"
        inputs = ["-loop", "1", "-framerate", str(fps), "-t", f"{D:.3f}", "-i", sc["slide"]]
        for x in subs:
            inputs += ["-loop", "1", "-framerate", str(fps), "-t", f"{D:.3f}", "-i", x["png"]]
        fc = (f"[0:v]scale={W}:{H},zoompan=z='{zexpr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={w}x{h}:fps={fps}[b0];")
        last = "b0"
        for k, x in enumerate(subs):
            fc += f"[{k+1}:v]scale={w}:{h}[s{k}];[{last}][s{k}]overlay=0:0:enable='between(t,{x['start']:.3f},{x['end']:.3f})'[b{k+1}];"
            last = f"b{k+1}"
        fd = 0.2
        fc += f"[{last}]fade=t=in:st=0:d={fd},fade=t=out:st={D - fd:.3f}:d={fd},format=yuv420p[v]"
        clip = os.path.join(edir, f"c{i:03d}.mp4")
        run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", fc, "-map", "[v]", "-frames:v", str(frames),
             "-c:v", "libx264", "-preset", "veryfast", "-crf", str(q["crf"]), "-r", str(fps), "-an", clip], f"シーン{i+1}の映像作成")
        t_cursor += D
        log(f"  シーン {i+1}/{len(scenes)} 完了（{D:.1f}秒）")
    # 結合
    lst = os.path.join(edir, "list.txt")
    with open(lst, "w") as f:
        for i in range(len(scenes)):
            f.write(f"file 'c{i:03d}.mp4'\n")
    vonly = os.path.join(edir, "video.mp4")
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", vonly], "映像の結合")
    nar_path = os.path.join(WORK, "narration.wav")
    write_wav(nar_path, np.concatenate(narr))
    bgm_path = os.path.join(WORK, "bgm.wav")
    make_bgm(bgm_path, t_cursor)
    os.makedirs(OUT, exist_ok=True)
    final = os.path.join(OUT, "video.mp4")
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", vonly, "-i", nar_path, "-i", bgm_path, "-filter_complex",
         "[1:a]aformat=channel_layouts=stereo,volume=1.0,asplit=2[n][nk];[2:a]aformat=channel_layouts=stereo,volume=0.18[b];"
         "[b][nk]sidechaincompress=threshold=0.03:ratio=6:attack=20:release=400[bd];"
         "[n][bd]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[a]",
         "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", str(SR),
         "-movflags", "+faststart", "-shortest", final], "音声とBGMの合成")
    # 字幕ファイル
    with open(os.path.join(OUT, "subtitles.srt"), "w", encoding="utf-8") as f:
        for k, (s, e, tx) in enumerate(srt_items, 1):
            f.write(f"{k}\n{srt_time(s)} --> {srt_time(e)}\n{tx}\n\n")
    st["duration"] = round(t_cursor, 2)
    st["chapters"] = chapters
    st["render"] = {"size": f"{w}x{h}", "fps": fps, "seconds_taken": round(time.time() - t0, 1)}
    save_state(st)
    summary(f"### STEP5-6 編集・書き出し\n- {w}×{h} / {fps}fps / {t_cursor:.1f}秒 / MP4(H.264+AAC)\n- 処理時間 {time.time() - t0:.0f}秒")


def srt_time(s):
    ms = int(round(s * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


# ---------------------------------------------------------------- STEP7 投稿用データ
def render_thumb(st, path):
    W, H = 1280, 720
    g = GENRES[st["genre"]]
    img = gradient_bg(W, H, *g["bg"], seed=99)
    d = ImageDraw.Draw(img)
    title = st["script"]["title"]
    parts = [x for x in re.split(r"(?<=[？！?!｜|])\s*", title) if x.strip()]
    if 1 < len(parts) <= 3:
        title = "\n".join(parts)
    f, lines, sz = fit_text(title, W - 160, H * 0.62, 120, minsize=48)
    tot = len(lines) * sz * 1.2
    y = (H - tot) / 2 - 10
    for l in lines:
        d.text((W / 2, y), l, font=f, fill=(255, 255, 255), anchor="ma", stroke_width=max(4, sz // 14), stroke_fill=(10, 10, 20))
        y += sz * 1.2
    d.rectangle([0, H - 26, W, H], fill=g["accent"])
    img.convert("RGB").save(path, quality=92)


def step_package(a):
    st = load_state()
    sc = st["script"]
    vert = FORMATS[st["format"]]["vertical"]
    tags = list(dict.fromkeys(sc.get("hashtags") or []))
    if vert and "Shorts" not in tags:
        tags.append("Shorts")
    render_thumb(st, os.path.join(OUT, "thumbnail.jpg"))
    chap = ""
    if not vert and len(st.get("chapters", [])) >= 3:
        chap = "\n\n▼ 目次\n" + "\n".join(f"{int(t)//60}:{int(t)%60:02d} {name}" for t, name in st["chapters"])
        if not chap.split("\n")[3].startswith("0:00"):
            chap = chap.replace("▼ 目次\n", "▼ 目次\n0:00 オープニング\n", 1)
    desc = (sc.get("description") or "") + chap + "\n\n" + " ".join("#" + t for t in tags) + \
        f"\n\n―――\nナレーション: {st['tts']['credit']}\n映像・図解・BGM: オリジナル制作"
    title = sc["title"]
    md = f"""# {title}

## YouTubeタイトル
{title}

## 説明文（そのままコピーできます）
```
{desc}
```

## ハッシュタグ
{' '.join('#' + t for t in tags)}

## 動画情報
- 形式: {FORMATS[st['format']]['label']} / {st['render']['size']} / {st['duration']}秒
- テーマ: {st['theme']}（{st['theme_source']}）
- 台本: {st['script_source']}

## 投稿前チェックリスト（収益化のため必ず確認）
- [ ] 台本の内容が事実として正しいか、自分で1回は確認した
- [ ] タイトル・サムネが内容と一致し、誇張や釣りになっていない
- [ ] 同じような動画を短期間に量産していない（テーマ・構成を変える）
- [ ] 説明文にナレーションのクレジット表記が入っている
- [ ] YouTube Studioの「改変または合成されたコンテンツ」は、実在の人物・出来事を本物らしく見せる内容でなければ通常「いいえ」でよい（迷ったら「はい」）
"""
    open(os.path.join(OUT, "metadata.md"), "w", encoding="utf-8").write(md)
    rights = rights_report(st)
    open(os.path.join(OUT, "rights.md"), "w", encoding="utf-8").write(rights)
    json.dump(sc, open(os.path.join(OUT, "script.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    open(os.path.join(OUT, "release_title.txt"), "w", encoding="utf-8").write(f"{st['created']} {title}")
    summary(f"### STEP7 投稿用データ\n- タイトル / 説明文 / ハッシュタグ / サムネ / 字幕(SRT) / 権利表\n\n" + rights)
    log("完成: " + os.path.abspath(os.path.join(OUT, "video.mp4")))


def rights_report(st):
    eng = st["tts"]["engine"]
    voice_row = {
        "voicevox": f"| ナレーション | VOICEVOX ENGINE（無料・オープンソース） | 商用可。**{st['tts']['credit']}** のクレジット表記が必須。キャラクターごとの利用規約に従うこと（公式サイトで確認） | ✅ |",
        "edge": "| ナレーション | Microsoft Edge 読み上げ（edge-tts 非公式ライブラリ経由） | 商用利用の許諾が明示されていない | ⚠️ 収益化動画では使わない |",
        "gtts": "| ナレーション | Google翻訳 読み上げ（gTTS 非公式ライブラリ経由） | 商用利用の許諾が明示されていない | ⚠️ 収益化動画では使わない |",
        "dummy": "| ナレーション | テスト用ダミー音 | 投稿不可 | ❌ |",
    }[eng]
    script_row = ("| 台本 | Google Gemini API（無料枠）で生成 | 出力の利用は可能だが、事実確認は投稿者の責任。無料枠の入力はGoogleの品質改善に使われる場合あり | ✅ 要事実確認 |"
                  if "Gemini" in st["script_source"] else f"| 台本 | {st['script_source']} | アプリ内蔵の文章 | ✅ |")
    return f"""## 権利・素材一覧（自動生成）
| 素材 | 出どころ | 条件 | 判定 |
|---|---|---|---|
{voice_row}
{script_row}
| 図解スライド・サムネ | このアプリが描画（外部画像なし） | 自作扱い | ✅ |
| フォント | Noto Sans CJK（SIL Open Font License） | 動画内での使用は商用可 | ✅ |
| BGM | このアプリが合成（外部音源なし） | 自作扱い | ✅ |
"""


# ---------------------------------------------------------------- 実行
def main():
    p = argparse.ArgumentParser()
    p.add_argument("step", choices=["plan", "script", "voice", "visuals", "edit", "package", "all"])
    p.add_argument("--theme", default="")
    p.add_argument("--format", default="test", choices=list(FORMATS))
    p.add_argument("--genre", default="雑学・科学")
    p.add_argument("--voice", default="")
    p.add_argument("--quality", default="", choices=["", *QUALITY])
    a = p.parse_args()
    if a.step in ("plan", "all") and not a.quality:
        a.quality = "draft"
    if not a.voice:
        a.voice = "zundamon"
    steps = {"plan": step_plan, "script": step_script, "voice": step_voice, "visuals": step_visuals, "edit": step_edit, "package": step_package}
    try:
        for name in (list(steps) if a.step == "all" else [a.step]):
            log(f"=== {name} ===")
            steps[name](a)
    except StepError as e:
        print(f"::error title=動画生成エラー::{str(e).splitlines()[0]}")
        log(str(e))
        summary(f"## ❌ エラー\n**原因:** {e}\n\n**対処:** {e.hint or 'もう一度 Run workflow を実行してください。'}")
        sys.exit(1)
    except Exception as e:
        print(f"::error title=予期しないエラー::{e}")
        summary(f"## ❌ 予期しないエラー\n```\n{e}\n```\n**対処:** もう一度実行してください。繰り返す場合はこの画面のスクリーンショットをClaudeに送ってください。")
        raise


if __name__ == "__main__":
    main()
