import concurrent.futures as cf
import html
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONF = json.loads((ROOT / "podcast.json").read_text(encoding="utf-8"))
REPO = os.environ.get("GITHUB_REPOSITORY", "Stalh/onco-du-matin")
OWNER, NAME = REPO.split("/")
SITE = f"https://{OWNER.lower()}.github.io/{NAME}"
DB = ROOT / "episodes.json"
RATE = 24000
GAPS = {"pause": 3.5, "section": 1.6, "chunk": 0.35}
MAX_CHARS = 3000
COVER_V = __import__("hashlib").md5((ROOT / "cover.png").read_bytes()).hexdigest()[:8]


# lit le front matter et le corps d'un épisode
def parse(path):
    raw = Path(path).read_text(encoding="utf-8").replace("\r\n", "\n")
    meta, body = {}, raw
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", raw, re.S)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
        body = m.group(2)
    meta.setdefault("date", Path(path).stem)
    meta.setdefault("title", meta["date"])
    meta.setdefault("summary", "")
    return meta, body.strip()


# découpe le script en morceaux de texte et silences
def segment(body):
    out, buf = [], ""
    for block in re.split(r"\n\s*\n", body):
        block = " ".join(block.split())
        if not block:
            continue
        tag = re.fullmatch(r"\[(pause|section)\]", block, re.I)
        if tag:
            if buf:
                out.append(("text", buf))
                buf = ""
            out.append(("gap", GAPS[tag.group(1).lower()]))
            continue
        block = re.sub(r"\[(pause|section)\]", " ", block, flags=re.I)
        if buf and len(buf) + len(block) + 2 > MAX_CHARS:
            out.append(("text", buf))
            out.append(("gap", GAPS["chunk"]))
            buf = ""
        buf = f"{buf}\n\n{block}" if buf else block
    if buf:
        out.append(("text", buf))
    return out


# synthèse vocale OpenAI en PCM 24 kHz mono
def tts(text, voice=None):
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        sys.exit("OPENAI_API_KEY manquant : ajoute-le dans Settings > Secrets and variables > Actions.")
    payload = json.dumps({
        "model": CONF["model"],
        "voice": voice or CONF["voice"],
        "input": text,
        "instructions": CONF["instructions"],
        "response_format": "pcm",
    }).encode()
    for attempt in range(4):
        req = urllib.request.Request(
            "https://api.openai.com/v1/audio/speech",
            data=payload,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            if e.code in (400, 401, 403, 404):
                sys.exit(f"OpenAI a refusé la requête ({e.code}) : {detail}")
            err = f"{e.code} {detail}"
        except Exception as e:
            err = str(e)
        time.sleep(5 * (attempt + 1))
    sys.exit(f"Échec de la synthèse vocale après 4 essais : {err}")


# assemble le PCM et encode en MP3 normalisé
def render(segments, mp3, voice=None):
    texts = [s for k, s in segments if k == "text"]
    with cf.ThreadPoolExecutor(4) as ex:
        audio = dict(zip(texts, ex.map(lambda t: tts(t, voice), texts)))
    pcm = bytearray()
    for kind, val in segments:
        pcm += audio[val] if kind == "text" else b"\x00\x00" * int(RATE * val)
    with tempfile.NamedTemporaryFile(suffix=".pcm") as f:
        f.write(pcm)
        f.flush()
        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error", "-f", "s16le", "-ar", str(RATE), "-ac", "1", "-i", f.name,
            "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-ar", str(RATE), "-ac", "1", "-b:a", "64k", str(mp3),
        ], check=True)
    return round(len(pcm) / (2 * RATE))


def gh(*args, check=True):
    return subprocess.run(["gh", *args, "--repo", REPO], check=check, capture_output=True, text=True)


# publie le MP3 comme asset d'une release GitHub
def publish(tag, mp3, title):
    if gh("release", "view", tag, check=False).returncode == 0:
        gh("release", "upload", tag, str(mp3), "--clobber")
    else:
        gh("release", "create", tag, str(mp3), "--title", title, "--notes", title, "--latest=false")
    return f"https://github.com/{REPO}/releases/download/{tag}/{mp3.name}"


def load():
    return json.loads(DB.read_text(encoding="utf-8")) if DB.exists() else []


def save(eps):
    eps.sort(key=lambda e: e["date"], reverse=True)
    DB.write_text(json.dumps(eps, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fmt_duration(s):
    return f"{s // 3600:d}:{s % 3600 // 60:02d}:{s % 60:02d}"


# copie les MP3 dans le site pour qu'ils soient servis en audio/mpeg
def stage_audio(eps, out):
    audio = out / "audio"
    audio.mkdir(parents=True, exist_ok=True)
    for ep in eps:
        name = ep["url"].rsplit("/", 1)[1]
        local = ROOT / "out" / name
        if local.exists():
            (audio / name).write_bytes(local.read_bytes())
        else:
            gh("release", "download", ep["tag"], "--pattern", name, "--dir", str(audio), "--clobber")
        ep["public"] = f"{SITE}/audio/{name}"


CSS = """
body{font-family:-apple-system,system-ui,sans-serif;max-width:720px;margin:0 auto;padding:24px 16px;background:#f6f4ef;color:#1d2a26;line-height:1.55}
a{color:#1f6b52}header{display:flex;gap:16px;align-items:center}header img{width:96px;border-radius:12px}
code{background:#e7e2d6;padding:2px 6px;border-radius:4px;word-break:break-all}
ul.eps{list-style:none;padding:0}ul.eps>li{border-top:1px solid #d8d2c4;padding:16px 0}ul.eps h2{font-size:1.05em;margin:0 0 6px}
.muted{color:#4a5752}audio{width:100%;margin-top:8px}
h1{font-size:1.5em;line-height:1.25}h2{font-size:1.2em;margin-top:1.6em;border-bottom:2px solid #7fc8a9;padding-bottom:4px}h3{font-size:1.05em;margin-top:1.3em}
.table{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:.92em;margin:12px 0}th,td{border:1px solid #d8d2c4;padding:6px 8px;text-align:left;vertical-align:top}th{background:#e7e2d6}
.warn{background:#fbecc8;border-left:4px solid #e9b44c;padding:10px 12px;border-radius:4px;margin:16px 0}
@media (prefers-color-scheme:dark){body{background:#131a18;color:#e8ece9}a{color:#7fc8a9}.muted{color:#a9b5b0}ul.eps>li,th,td{border-color:#2c3633}th,code{background:#25302d}.warn{background:#3a3320}}
"""


def page(title, body):
    e = html.escape
    return f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(title)}</title><style>{CSS}</style></head><body>
{body}
</body></html>
"""


# rend chaque fiche écrite en page web
def render_fiches(eps, out):
    import markdown
    (out / "fiches").mkdir(parents=True, exist_ok=True)
    for ep in eps:
        src = ROOT / "notes" / f"{ep['date']}.md"
        if not src.exists():
            continue
        body = markdown.markdown(src.read_text(encoding="utf-8"), extensions=["tables", "sane_lists"])
        body = re.sub(r"(<table>.*?</table>)", r'<div class="table">\1</div>', body, flags=re.S)
        warn = '<p class="warn">Doses indicatives, à vérifier dans le thésaurus et les protocoles validés du centre avant toute prescription.</p>'
        nav = f'<p class="muted"><a href="../">{html.escape(CONF["title"])}</a> · {html.escape(ep["date"])}</p>'
        (out / "fiches" / f"{ep['date']}.html").write_text(page(ep["title"], nav + warn + body), encoding="utf-8")
        ep["fiche"] = f"{SITE}/fiches/{ep['date']}.html"


# génère le flux RSS et la page d'accueil
def site(eps):
    out = ROOT / "site"
    out.mkdir(exist_ok=True)
    (out / "cover.png").write_bytes((ROOT / "cover.png").read_bytes())
    stage_audio(eps, out)
    render_fiches(eps, out)
    e = html.escape

    def notes(ep):
        h = f"<p>{e(ep['summary'])}</p>"
        if ep.get("fiche"):
            h += f'<p><a href="{e(ep["fiche"])}">Lire la fiche complète : protocoles, doses et sources</a></p>'
        return h

    def desc(ep):
        return ep["summary"] + (f" Fiche complète : {ep['fiche']}" if ep.get("fiche") else "")

    items = "".join(f"""
    <item>
      <title>{e(ep['title'])}</title>
      <description>{e(desc(ep))}</description>
      <content:encoded><![CDATA[{notes(ep)}]]></content:encoded>
      <itunes:summary>{e(desc(ep))}</itunes:summary>
      <link>{e(ep.get('fiche') or SITE + '/')}</link>
      <pubDate>{ep['pub']}</pubDate>
      <guid isPermaLink="false">onco-{ep['date']}-{ep.get('rev', 1)}</guid>
      <enclosure url="{e(ep['public'])}" length="{ep['bytes']}" type="audio/mpeg"/>
      <itunes:duration>{fmt_duration(ep['duration'])}</itunes:duration>
      <itunes:episodeType>full</itunes:episodeType>
      <itunes:explicit>false</itunes:explicit>
    </item>""" for ep in eps)
    last = eps[0]["pub"] if eps else format_datetime(datetime.now(timezone.utc))
    (out / "feed.xml").write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" xmlns:content="http://purl.org/rss/1.0/modules/content/" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>{e(CONF['title'])}</title>
    <link>{SITE}/</link>
    <atom:link href="{SITE}/feed.xml" rel="self" type="application/rss+xml"/>
    <language>{CONF['language']}</language>
    <description>{e(CONF['description'])}</description>
    <itunes:summary>{e(CONF['description'])}</itunes:summary>
    <itunes:author>{e(CONF['author'])}</itunes:author>
    <itunes:image href="{SITE}/cover.png?v={COVER_V}"/>
    <image><url>{SITE}/cover.png?v={COVER_V}</url><title>{e(CONF['title'])}</title><link>{SITE}/</link></image>
    <itunes:category text="Health &amp; Fitness"><itunes:category text="Medicine"/></itunes:category>
    <itunes:explicit>false</itunes:explicit>
    <itunes:type>episodic</itunes:type>
    <itunes:block>Yes</itunes:block>
    <lastBuildDate>{last}</lastBuildDate>{items}
  </channel>
</rss>
""", encoding="utf-8")
    rows = "".join(
        f"<li><h2>{e(ep['title'])}</h2><p class=\"muted\">{e(ep['summary'])}</p>"
        + (f"<p><a href=\"{e(ep['fiche'])}\">Fiche complète</a></p>" if ep.get("fiche") else "")
        + f"<audio controls preload=\"none\" src=\"{e(ep['public'])}\"></audio></li>" for ep in eps
    )
    head = (f'<header><img src="cover.png" alt=""><div><h1>{e(CONF["title"])}</h1>'
            f'<p class="muted">{e(CONF["description"])}</p></div></header>'
            f'<p>Flux à ajouter dans l\'app Podcasts : <code>{SITE}/feed.xml</code></p>')
    (out / "index.html").write_text(page(CONF["title"], head + f'<ul class="eps">{rows}</ul>'), encoding="utf-8")


# supprime les épisodes au-delà de la limite
def prune(eps):
    keep = int(CONF.get("keep", 30))
    for ep in eps[keep:]:
        gh("release", "delete", ep["tag"], "--yes", "--cleanup-tag", check=False)
    return eps[:keep]


def build(path):
    meta, body = parse(path)
    segs = segment(body)
    if not any(k == "text" for k, _ in segs):
        sys.exit(f"{path} : script vide.")
    date = meta["date"]
    tag = f"ep-{date}"
    work = ROOT / "out"
    work.mkdir(exist_ok=True)
    mp3 = work / f"onco-du-matin-{date}.mp3"
    print(f"{path} : {sum(len(s) for k, s in segs if k == 'text')} caractères, {sum(k == 'text' for k, _ in segs)} morceaux")
    duration = render(segs, mp3)
    url = publish(tag, mp3, meta["title"])
    eps = [e for e in load() if e["date"] != date]
    prev = next((e for e in load() if e["date"] == date), None)
    eps.append({
        "date": date,
        "title": meta["title"],
        "summary": meta["summary"],
        "tag": tag,
        "url": url,
        "bytes": mp3.stat().st_size,
        "duration": duration,
        "pub": prev["pub"] if prev else format_datetime(datetime.now(timezone.utc)),
        "rev": prev.get("rev", 1) + 1 if prev else 1,
    })
    eps.sort(key=lambda e: e["date"], reverse=True)
    save(prune(eps))
    print(f"{date} : {fmt_duration(duration)}, {mp3.stat().st_size // 1024} Ko")


# extraits de 4 voix pour choisir
def samples():
    text = parse(sorted((ROOT / "episodes").glob("*.md"))[-1])[1]
    text = " ".join(text.split("\n\n")[:3])[:900]
    work = ROOT / "out"
    work.mkdir(exist_ok=True)
    files = []
    for voice in ["coral", "sage", "ash", "ballad", "marin", "cedar"]:
        mp3 = work / f"voix-{voice}.mp3"
        try:
            render([("text", text)], mp3, voice)
            files.append(str(mp3))
        except SystemExit as e:
            print(f"{voice} : {e}")
    gh("release", "delete", "voix", "--yes", "--cleanup-tag", check=False)
    gh("release", "create", "voix", *files, "--title", "Échantillons de voix", "--notes", "Écoute et choisis", "--latest=false")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args == ["--samples"]:
        samples()
    else:
        for p in args:
            build(p)
    site(load())
