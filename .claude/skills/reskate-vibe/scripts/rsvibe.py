#!/usr/bin/env python3
"""Public playlist -> packed ReSkate music mod.

  rsvibe.py doctor                      check tools, game folder and packer
  rsvibe.py list <playlist-url> <work>  write <work>/tracks.json
  rsvibe.py fetch <work>                download + tag every track into <work>/songs/ (resumable)
  rsvibe.py check <work>                verify songs/ matches tracks.json exactly
  rsvibe.py pack <work> [game-folder] [-- extra packer args]
                                        build <work>/mod with ReSkateMusicPacker

Every command is safe to re-run. Needs Python 3.8+, yt-dlp and ffmpeg/ffprobe on PATH.
Env: RSVIBE_GAME (game folder), RSVIBE_PACKER (own packer binary),
     RSVIBE_YTDLP_ARGS (extra yt-dlp args, e.g. "--cookies-from-browser firefox").
"""
import argparse, concurrent.futures, datetime, difflib, hashlib, json, os, platform, re, shlex, shutil
import subprocess, sys, tarfile, tempfile, urllib.error, urllib.parse, urllib.request, zipfile
from pathlib import Path
from typing import NoReturn

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"}
EXT = ".mka"  # Matroska holds any codec, so audio is copied losslessly and tags land where the packer reads them
PACKER_TAG = "v1.1.2-crossplatform.3"
PACKER_URL = f"https://github.com/ValveTextureFile/ReSkateMusicPacker/releases/download/{PACKER_TAG}/ReSkateMusicPacker-{PACKER_TAG}-"
PACKER_ASSETS = {  # os -> (asset suffix, sha256, binary path inside the archive)
    "linux": ("linux-x86_64.tar.gz", "74eafa88bf85785d13a380ed99538e98d16f229de5f4de81819bad3b57c1bdda", "ReSkateMusicPacker"),
    "darwin": ("macos-universal.zip", "373a41b85c99052f40acf0a27333e9ccc9e49d5ef4b5120f9970fa1ce89bfa89", "ReSkateMusicPacker.app/Contents/MacOS/ReSkateMusicPacker"),
    "win32": ("win64.zip", "e0596d93ec08a96ebed7682a79ff5a7ce73791301c77516be246505d12544b70", "ReSkateMusicPacker.exe"),
}
MAX_SECONDS = 1800
SPOTIFY_RE = r"spotify(?:\.com)?[:/](?:intl-\w+/)?(?:user[:/][^/:]+[:/])?(playlist|album)[:/](\w+)"

for stream in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252 and choke on track names
    if hasattr(stream, "reconfigure"):
        getattr(stream, "reconfigure")(encoding="utf-8", errors="replace", line_buffering=True)


def die(msg) -> NoReturn:
    sys.exit(f"error: {msg}")


def run(cmd, **kw):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **kw)
    except FileNotFoundError:
        die(f"'{cmd[0]}' is not installed or not on PATH. Run `rsvibe.py doctor`.")


def ytdlp_cmd(*args):
    return ["yt-dlp", "--no-warnings", *shlex.split(os.environ.get("RSVIBE_YTDLP_ARGS", "")), *args]


def get(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
            return r.geturl(), r.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        die(f"could not fetch {url}: {e}. Check the link and your connection; the playlist must be public.")


def find(pattern, text, what):
    m = re.search(pattern, text, re.S)
    if not m:
        die(f"could not find {what}. Check the link, or the site's page layout changed.")
    return m


def tidy(text):
    """One line, no control characters, single spaces: what the packer accepts as a name."""
    return re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f]", " ", str(text or ""))).strip()


def song_id(t):
    return f"{t['artist']} - {t['title']}"


def fold(text):
    # The packer folds smart quotes and compares case-insensitively, so duplicates must be found the same way.
    return text.translate(str.maketrans("‘’‚‛“”„‟", "''''\"\"\"\"")).casefold()


def loose(text):
    return re.sub(r"[^0-9a-z]+", "", fold(text))


def safe(text):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text).strip(" .")[:100]


def stem_rest(t):
    return f"{safe(t['artist'])} - {safe(t['title'])}"


NOISE = re.compile(r"\s*[\(\[]([^\)\]]*\b(official|video|audio|lyrics?|visuali[sz]er|hd|hq|4k|mv|m/v|clip|videoclip|oficial)\b[^\)\]]*)[\)\]]", re.I)


def clean_video_title(title):
    return tidy(NOISE.sub("", title).replace("| Topic", ""))


# ---------------------------------------------------------------- list

def spotify(url):
    kind, pid = find(SPOTIFY_RE, url, "a Spotify playlist/album id").groups()
    _, html = get(f"https://open.spotify.com/embed/{kind}/{pid}")
    data = json.loads(find(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, "the Spotify tracklist").group(1))
    e = data["props"]["pageProps"]["state"]["data"]["entity"]
    tracks = [{"artist": t.get("subtitle"), "title": t.get("title"), "duration": (t.get("duration") or 0) / 1000 or None} for t in e.get("trackList", [])]
    if len(tracks) >= 100:  # shortcut: the anonymous embed page stops at 100 tracks, upgrade if Spotify ever exposes paging without a login
        print("warning: Spotify only shows the first 100 tracks without a login. Ask the user for the rest if the playlist is longer.")
    return e.get("name") or "Spotify Playlist", tracks


def apple(url):
    _, html = get(url)
    data = json.loads(find(r'id="serialized-server-data"[^>]*>(.*?)</script>', html, "the Apple Music tracklist").group(1))
    sections = data["data"][0]["data"]["sections"]
    tracks = [{"artist": i.get("artistName"), "title": i.get("title"), "duration": (i.get("duration") or 0) / 1000 or None}
              for s in sections if s.get("itemKind") == "trackLockup" for i in s.get("items", [])]
    header = next((s["items"][0] for s in sections if s.get("itemKind") == "containerDetailHeaderLockup" and s.get("items")), {})
    if len(tracks) >= 100:  # shortcut: the public page renders 100 tracks at most, upgrade if longer playlists become common
        print("warning: Apple Music's page shows at most 100 tracks. Ask the user for the rest if the playlist is longer.")
    return header.get("title") or "Apple Music Playlist", tracks


def deezer(url):
    kind, pid = find(r"deezer\.com/(?:\w+/)?(playlist|album)/(\d+)", url, "a Deezer playlist/album id").groups()
    d = json.loads(get(f"https://api.deezer.com/{kind}/{pid}")[1])
    if "error" in d:
        die(f"Deezer: {d['error'].get('message', d['error'])}. Is the {kind} public?")
    page, tracks = d["tracks"], []
    while True:
        tracks += [{"artist": t["artist"]["name"], "title": t["title"], "duration": t.get("duration")} for t in page["data"]]
        if not page.get("next"):
            break
        page = json.loads(get(page["next"])[1])
    return d["title"], tracks


def ytdlp_list(url):
    # YouTube, YouTube Music, SoundCloud, Bandcamp and anything else yt-dlp can list.
    p = run(ytdlp_cmd("-J", "--flat-playlist", "--", url))
    if p.returncode:
        die(f"yt-dlp could not read {url}: {(p.stderr.strip().splitlines() or ['no output'])[-1]}")
    d = json.loads(p.stdout)
    tracks = []
    for e in d.get("entries") or [d]:
        if not e or e.get("title") in ("[Deleted video]", "[Private video]", None):
            print(f"skipped an unavailable entry: {e and e.get('url')}")
            continue
        title = e.get("track") or e.get("title") or ""
        artist = e.get("artist") or (e.get("artists") or [None])[0]
        if not artist and " - " in title:
            artist, title = title.split(" - ", 1)
        artist = artist or re.sub(r"\s*-\s*Topic$", "", e.get("channel") or e.get("uploader") or "")
        tracks.append({"artist": clean_video_title(artist), "title": clean_video_title(title),
                       "duration": e.get("duration"), "source": e.get("webpage_url") or e.get("url")})
    return d.get("title") or "Playlist", tracks


def resolve(url):
    url = url.strip()
    if url.startswith("spotify:"):
        return url
    if not re.match(r"https?://", url):
        url = "https://" + url
    host = urllib.parse.urlparse(url).netloc.lower()
    if host in ("spotify.link", "deezer.page.link", "link.deezer.com") or host.endswith(".app.link"):
        final, html = get(url)  # short links redirect, sometimes through an HTML page
        m = re.search(r"https://(?:open\.spotify\.com|www\.deezer\.com)/[^\"'\s<>]+", final + " " + html)
        url = m.group(0) if m else final
    return url


def cmd_list(a):
    url, work = resolve(a.url), Path(a.work)
    host = urllib.parse.urlparse(url).netloc.lower() if "://" in url else ""
    reader = (spotify if url.startswith("spotify:") or host.endswith("spotify.com") else apple if host.endswith("music.apple.com")
              else deezer if host.endswith("deezer.com") else ytdlp_list)
    try:
        name, tracks = reader(url)
    except (KeyError, IndexError, TypeError, ValueError) as e:
        die(f"the page for {url} did not have the expected shape ({type(e).__name__}: {e}). The site may have changed; "
            "read the tracklist another way and write tracks.json by hand.")
    tracks = [{k: v for k, v in {**t, "artist": tidy(t.get("artist")), "title": tidy(t.get("title"))}.items() if v not in (None, "")}
              for t in tracks]
    if not tracks:
        die(f"no tracks found at {url}. Is the playlist public and not empty?")
    work.mkdir(parents=True, exist_ok=True)
    out = work / "tracks.json"
    if out.exists():
        shutil.copy2(out, work / "tracks.json.bak")
        print(f"note: existing tracks.json backed up to tracks.json.bak")
    out.write_text(json.dumps({"name": tidy(name), "url": a.url, "tracks": tracks}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    problems = validate({"name": tidy(name), "tracks": tracks})
    print(f"{tidy(name)}: {len(tracks)} tracks -> {out}")
    if problems:
        print("fix these in tracks.json before `fetch`:\n  " + "\n  ".join(problems))


# ---------------------------------------------------------------- plan

def validate(plan):
    problems = []
    name = plan.get("name")
    if not isinstance(name, str) or not 1 <= len(tidy(name)) <= 255 or tidy(name) != name:
        problems.append(f"name {name!r}: must be 1-255 characters on one line, without leading/trailing spaces")
    tracks = plan.get("tracks")
    if not isinstance(tracks, list) or not tracks:
        return problems + ["tracks: must be a non-empty list"]
    ids, stems = {}, {}
    for n, t in enumerate(tracks, 1):
        if not isinstance(t, dict):
            problems.append(f"track {n}: must be an object with artist and title")
            continue
        for field in ("artist", "title"):
            v = t.get(field)
            if not isinstance(v, str) or not v.strip():
                problems.append(f"track {n}: {field} is missing or empty")
            elif tidy(v) != v:
                problems.append(f"track {n}: {field} {v!r} has control characters, doubled or edge spaces; use {tidy(v)!r}")
            elif len(v.encode("utf-8")) > 255:
                problems.append(f"track {n}: {field} is longer than 255 bytes")
        if "source" in t and t["source"] is not None and not (isinstance(t["source"], str) and re.match(r"https?://", t["source"])):
            problems.append(f"track {n}: source must be an http(s) URL or left out")
        if "duration" in t and t["duration"] is not None and not isinstance(t["duration"], (int, float)):
            problems.append(f"track {n}: duration must be seconds as a number or left out")
        if isinstance(t.get("artist"), str) and isinstance(t.get("title"), str):
            for seen, key, what in ((ids, fold(song_id(t)), "song id"), (stems, stem_rest(t).casefold(), "file name")):
                if key in seen:
                    problems.append(f"track {n}: same {what} as track {seen[key]} ({song_id(t)!r}); remove one or make the titles differ")
                seen.setdefault(key, n)
    return problems


def load_plan(work):
    path = Path(work) / "tracks.json"
    if not path.exists():
        die(f"{path} not found. Run `rsvibe.py list <url> {work}` first, or write it by hand.")
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as e:
        die(f"{path} is not valid JSON: {e}")
    if not isinstance(plan, dict):
        die(f"{path} must be an object with name and tracks")
    problems = validate(plan)
    if problems:
        die(f"{path} has {len(problems)} problem(s):\n  " + "\n  ".join(problems))
    return plan


# ---------------------------------------------------------------- fetch

def probe(path):
    p = run(["ffprobe", "-v", "error", "-show_entries", "format=duration:format_tags=artist,title", "-of", "json", str(path)])
    try:
        f = json.loads(p.stdout)["format"]
    except (ValueError, KeyError):
        return None, {}
    tags = {k.lower(): v for k, v in f.get("tags", {}).items()}
    return float(f.get("duration") or 0) or None, tags


def close(actual, expected):
    return expected is None or (actual is not None and abs(actual - expected) <= max(8, expected * 0.08))


def write_tagged(src, t, dst):
    """Copy the audio losslessly into dst with exactly the artist/title tags. Atomic, so an interrupted run never leaves a half file."""
    tmp = dst.with_name(dst.stem + ".part" + EXT)
    for codec in ("copy", "flac"):  # flac only if a stream refuses to be copied
        p = run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(src), "-map", "0:a:0", "-vn", "-map_metadata", "-1", "-c:a", codec,
                 "-metadata", f"artist={t['artist']}", "-metadata", f"title={t['title']}", str(tmp)])
        if p.returncode == 0:
            os.replace(tmp, dst)
            return
    tmp.unlink(missing_ok=True)
    raise RuntimeError(f"ffmpeg could not convert the download: {p.stderr.strip()[-300:]}")


def search(t):
    """Candidate URLs, best first: YouTube Music songs whose title matches, then YouTube results ranked by duration."""
    want, exp, cands = loose(re.sub(r"\s*[\(\[].*?[\)\]]", "", t["title"])) or loose(t["title"]), t.get("duration"), []
    q = f"{t['artist']} {t['title']}"
    p = run(ytdlp_cmd("-J", "--flat-playlist", "--playlist-items", "1:5", "--",
                      "https://music.youtube.com/search?q=" + urllib.parse.quote(q) + "#songs"), timeout=120)
    if p.returncode == 0:
        for e in (json.loads(p.stdout).get("entries") or []):
            if e and e.get("url") and (want in loose(e.get("title", "")) or difflib.SequenceMatcher(None, want, loose(e.get("title", ""))).ratio() > 0.6):
                cands.append(e["url"])
    p = run(ytdlp_cmd("-J", "--flat-playlist", "--", f"ytsearch5:{t['artist']} - {t['title']} audio"), timeout=120)
    if p.returncode == 0:
        hits = [e for e in (json.loads(p.stdout).get("entries") or []) if e and e.get("url") and want in loose(e.get("title", ""))]
        if exp:
            hits.sort(key=lambda e: abs((e.get("duration") or 1e9) - exp))
        cands += [e["url"] for e in hits]
    out, seen = [], set()
    for u in cands:
        vid = re.search(r"[?&]v=([\w-]{11})", u)
        k = vid.group(1) if vid else u
        if k not in seen:
            seen.add(k)
            out.append(u)
    return out[:4]


def fetch_one(t, final, tmpdir):
    exp, best, why = t.get("duration"), None, "no results"
    urls = [t["source"]] if t.get("source") else search(t)
    for i, url in enumerate(urls):
        out = tmpdir / str(i)
        p = run(ytdlp_cmd("-q", "--no-playlist", "-f", "bestaudio/best", "--match-filter", f"!is_live & duration <? {MAX_SECONDS}",
                          "-o", str(out / "a.%(ext)s"), "--", url), timeout=900)
        got = next((f for f in out.glob("a.*") if not f.name.endswith(".part")), None) if out.exists() else None
        if p.returncode or not got:
            why = (p.stderr.strip().splitlines() or [f"filtered out (live, or longer than {MAX_SECONDS // 60} min)"])[-1]
            continue
        dur, _ = probe(got)
        if best is None or (exp and dur and abs(dur - exp) < abs((best[2] or 1e9) - exp)):
            best = (url, got, dur)
        if close(dur, exp):
            break
    if not best:
        hint = " (YouTube wants a login: set RSVIBE_YTDLP_ARGS='--cookies-from-browser firefox')" if "Sign in" in why else ""
        return {"status": "failed", "reason": why + hint, "tried": urls}
    url, got, dur = best
    write_tagged(got, t, final)
    ok = close(dur, exp)
    return {"status": "ok" if ok else "mismatch", "url": url, "duration": round(dur or 0, 1),
            **({} if ok else {"expected": exp, "reason": "duration differs from the original; may be a live, cover or edited version"})}


def cmd_fetch(a):
    work = Path(a.work)
    plan = load_plan(work)
    songs = work / "songs"
    songs.mkdir(exist_ok=True)
    tmproot = work / ".tmp"  # inside work, so a hard-killed run's leftovers are cleared by the next one
    shutil.rmtree(tmproot, ignore_errors=True)
    tmproot.mkdir()
    state_path = work / "fetched.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    tracks = plan["tracks"]
    want = {stem_rest(t).casefold(): (n, t) for n, t in enumerate(tracks, 1)}

    # Reconcile: keep, renumber or retag what is already there; drop anything the plan no longer has.
    have, removed = {}, []
    for f in sorted(songs.iterdir()):
        m = re.fullmatch(r"\d{3} (.+)" + re.escape(EXT), f.name)
        key = m and m.group(1).casefold()
        entry = want.get(key) if key else None
        stale = entry and entry[1].get("source") and state.get(key, {}).get("requested") != entry[1]["source"]
        if not entry or stale or not f.is_file():
            shutil.rmtree(f) if f.is_dir() else f.unlink()
            removed.append(f.name)
            state.pop(key, None) if key else None
            continue
        have[key] = f
    for key, f in have.items():
        n, t = want[key]
        target = songs / f"{n:03d} {stem_rest(t)}{EXT}"
        _, tags = probe(f)
        if tags.get("artist") != t["artist"] or tags.get("title") != t["title"]:
            write_tagged(f, t, target)
            if f != target:
                f.unlink()
        elif f != target:
            os.replace(f, target)
    if removed:
        print(f"removed {len(removed)} file(s) no longer in tracks.json: {', '.join(removed)}")

    todo = [(n, t) for key, (n, t) in want.items() if key not in have]
    print(f"{len(tracks) - len(todo)} already here, downloading {len(todo)} with {a.jobs} parallel job(s). "
          "If this gets interrupted, run the same command again; it resumes.")
    report = {e["n"]: e for e in json.loads((work / "report.json").read_text(encoding="utf-8"))} if (work / "report.json").exists() else {}
    report = {n: report[n] for n in report if n <= len(tracks) and report[n].get("id") == song_id(tracks[n - 1])}

    def job(nt):
        n, t = nt
        tmpdir = Path(tempfile.mkdtemp(prefix=f"{n:03d}-", dir=tmproot))
        try:
            return n, t, fetch_one(t, songs / f"{n:03d} {stem_rest(t)}{EXT}", tmpdir)
        except Exception as e:  # one bad track must not sink the whole run
            return n, t, {"status": "failed", "reason": f"{type(e).__name__}: {e}"}
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, a.jobs)) as pool:
        for n, t, r in pool.map(job, todo):
            report[n] = {"n": n, "id": song_id(t), **r}
            if r["status"] != "failed":
                state[stem_rest(t).casefold()] = {"requested": t.get("source"), "url": r["url"]}
                state_path.write_text(json.dumps(state, indent=1, ensure_ascii=False), encoding="utf-8")
            print(f"{r['status']:8} {n:3}: {song_id(t)}" + (f"  ({r['reason']})" if r.get("reason") else ""))

    shutil.rmtree(tmproot, ignore_errors=True)
    for n, t in enumerate(tracks, 1):
        report.setdefault(n, {"n": n, "id": song_id(t), "status": "ok"})
    rows = [report[n] for n in sorted(report)]
    (work / "report.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    failed = [r for r in rows if r["status"] == "failed"]
    mism = [r for r in rows if r["status"] == "mismatch"]
    print(f"\n{len(tracks) - len(failed)}/{len(tracks)} tracks ready, {len(failed)} failed, {len(mism)} with a duration mismatch. Details: {work / 'report.json'}")
    for r in failed:
        print(f"  FAILED   {r['n']:3}: {r['id']}  -> fix the name or set a source URL in tracks.json, or remove the track; then fetch again")
    for r in mism:
        print(f"  MISMATCH {r['n']:3}: {r['id']}  got {r['duration']}s, expected {r['expected']}s from {r['url']}  -> set a better source or accept it")
    sys.exit(1 if failed else 0)


# ---------------------------------------------------------------- check

def problems_in(work, plan):
    songs, out = Path(work) / "songs", []
    expected = {f"{n:03d} {stem_rest(t)}{EXT}": t for n, t in enumerate(plan["tracks"], 1)}
    present = {f.name for f in songs.iterdir()} if songs.exists() else set()
    for name in sorted(present - set(expected)):
        out.append(f"extra file songs/{name}: the packer would include it. Run fetch again to clean up, or delete it")
    for name, t in expected.items():
        if name not in present:
            out.append(f"missing songs/{name}: run fetch, or remove the track from tracks.json")
            continue
        dur, tags = probe(songs / name)
        if tags.get("artist") != t["artist"] or tags.get("title") != t["title"]:
            out.append(f"songs/{name}: tags {tags.get('artist')!r} - {tags.get('title')!r} do not match tracks.json; run fetch again")
        if not dur:
            out.append(f"songs/{name}: unreadable audio; delete it and run fetch again")
        elif dur > MAX_SECONDS:
            out.append(f"songs/{name}: {dur:.0f}s long, longer than {MAX_SECONDS // 60} min")
    return out


def cmd_check(a):
    plan = load_plan(a.work)
    bad = problems_in(a.work, plan)
    for p in bad:
        print(f"BAD {p}")
    print(f"{len(plan['tracks'])} tracks in tracks.json, {len(bad)} problem(s)")
    sys.exit(1 if bad else 0)


# ---------------------------------------------------------------- pack

def steam_roots():
    home = Path.home()
    roots = [home / ".local/share/Steam", home / ".steam/steam", home / ".var/app/com.valvesoftware.Steam/.local/share/Steam",
             home / "Library/Application Support/Steam", Path("C:/Program Files (x86)/Steam"), Path("C:/Program Files/Steam")]
    libs = []
    for r in roots:
        vdf = r / "steamapps/libraryfolders.vdf"
        extra = re.findall(r'"path"\s+"([^"]+)"', vdf.read_text(encoding="utf-8", errors="replace")) if vdf.is_file() else []
        libs += [r] + [Path(p.replace("\\\\", "\\")) for p in extra]
    return libs


def find_game(given=None):
    cands = [Path(given)] if given else (
        ([Path(os.environ["RSVIBE_GAME"])] if os.environ.get("RSVIBE_GAME") else [])
        + [lib / "steamapps/common/Skate" for lib in steam_roots()]
        + [Path("C:/Program Files/EA Games/Skate"), Path("C:/Program Files/Electronic Arts/Skate")])
    for c in cands:
        if (c / "Skate.exe").is_file():
            if not (c / "Data/layout.toc").is_file():
                die(f"{c} has Skate.exe but no Data/layout.toc; is the game fully installed?")
            return c.resolve()
    if given:
        die(f"{given} does not contain Skate.exe. Pass the folder that has Skate.exe in it.")
    return None


def cache_dir():
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "rsvibe"


def get_packer():
    if os.environ.get("RSVIBE_PACKER"):
        p = Path(os.environ["RSVIBE_PACKER"])
        return p if p.is_file() else die(f"RSVIBE_PACKER={p} does not exist")
    key = "win32" if sys.platform == "win32" else sys.platform
    if key not in PACKER_ASSETS or (key == "linux" and platform.machine().lower() not in ("x86_64", "amd64")):
        die(f"no prebuilt packer for {sys.platform}/{platform.machine()}. Build it from "
            "https://github.com/ValveTextureFile/ReSkateMusicPacker/tree/cross-platform and set RSVIBE_PACKER to the binary.")
    suffix, sha, inner = PACKER_ASSETS[key]
    home = cache_dir() / PACKER_TAG
    hits = [p for p in home.rglob(Path(inner).name) if p.as_posix().endswith(inner)] if home.exists() else []
    if hits:
        return hits[0]
    print(f"downloading ReSkateMusicPacker {PACKER_TAG} ...")
    try:
        with urllib.request.urlopen(PACKER_URL + suffix, timeout=120) as r:
            blob = r.read()
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        die(f"could not download the packer: {e}")
    if hashlib.sha256(blob).hexdigest() != sha:
        die("the downloaded packer does not match its pinned SHA-256; refusing to run it")
    home.mkdir(parents=True, exist_ok=True)
    archive = home / suffix
    archive.write_bytes(blob)
    if suffix.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            z.extractall(home)
    else:
        with tarfile.open(archive) as t:
            if hasattr(tarfile, "data_filter"):  # refuses path traversal; older Pythons lack it
                t.extractall(home, filter="data")
            else:
                t.extractall(home)
    archive.unlink()
    hits = [p for p in home.rglob(Path(inner).name) if p.as_posix().endswith(inner)]
    if not hits:
        die(f"the packer archive has no {inner}")
    hits[0].chmod(0o755)  # zip extraction drops the executable bit
    return hits[0]


def cmd_pack(a):
    work = Path(a.work).resolve()
    plan = load_plan(work)
    bad = problems_in(work, plan)
    if bad:
        die("songs/ is not ready:\n  " + "\n  ".join(bad))
    game = find_game(a.game)
    if not game:
        die("could not find the skate. folder (the one with Skate.exe). Pass it: rsvibe.py pack <work> <game folder>")
    if not (game / "ReSkate.dll").exists():
        print(f"warning: {game} has no ReSkate.dll; the mod only works in a ReSkate install")
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            die(f"{tool} must be on PATH for the packer")
    packer = get_packer()
    mod = work / "mod"
    if mod.exists():
        if not (mod / "reskate-build.json").exists():
            die(f"{mod} exists and is not a previous build; move it away first")
        shutil.rmtree(mod)
    extra = a.extra
    name = plan["name"]
    cmd = [str(packer), str(game), str(work / "songs"), str(mod), "--name", name, "--playlist", name]
    if not any(x in extra for x in ("--playlist-artwork", "--generate-playlist-artwork")):
        cmd += ["--generate-playlist-artwork", name]
    print(f"packing {len(plan['tracks'])} songs with {packer.name} against {game} (encoding takes a while) ...")
    p = subprocess.run(cmd + extra)
    if p.returncode:
        die(f"the packer failed with exit code {p.returncode}; its output is above")
    try:
        built = json.loads((mod / "reskate-music.json").read_text(encoding="utf-8"))
        count = sum(len(pl["songs"]) for pl in built["playlists"])
    except (OSError, ValueError, KeyError) as e:
        die(f"the packer finished but {mod / 'reskate-music.json'} is missing or unreadable: {e}")
    if count != len(plan["tracks"]):
        die(f"the mod has {count} songs but tracks.json has {len(plan['tracks'])}")
    print(f"\nbuilt {mod} with {count} songs in playlist {name!r}.\n"
          f"install: copy that folder into {game / 'Mods'} (or use the ReSkate launcher's MODS page) and restart the game.")


# ---------------------------------------------------------------- doctor

def cmd_doctor(_args):
    bad = 0

    def line(ok, what, hint=""):
        nonlocal bad
        bad += ok is False
        print(f"{ {True: 'ok  ', False: 'FAIL', None: 'warn'}[ok]} {what}" + (f"  -> {hint}" if hint and ok is not True else ""))

    line(sys.version_info >= (3, 8), f"python {platform.python_version()}", "needs 3.8+")
    for tool in ("yt-dlp", "ffmpeg", "ffprobe"):
        line(bool(shutil.which(tool)), tool, "install it and put it on PATH")
    if shutil.which("yt-dlp"):
        v = run(["yt-dlp", "--version"]).stdout.strip()
        try:
            age = (datetime.date.today() - datetime.date(*map(int, v.split(".")[:3]))).days
            line(True if age < 90 else None, f"yt-dlp {v}", "" if age < 90 else "old yt-dlp breaks on YouTube changes; update it (yt-dlp -U or your package manager)")
        except ValueError:
            line(None, f"yt-dlp {v}", "unknown version format")
    game = find_game()
    line(True if game else None, f"game folder: {game}" if game else "game folder not found automatically", "" if game else "pass it to `pack`, or set RSVIBE_GAME")
    if game:
        line(True if (game / "ReSkate.dll").exists() else None, "ReSkate installed", "" if (game / "ReSkate.dll").exists() else "no ReSkate.dll in the game folder")
    key = "win32" if sys.platform == "win32" else sys.platform
    line(True if os.environ.get("RSVIBE_PACKER") or key in PACKER_ASSETS else None, f"packer for {sys.platform}/{platform.machine()}",
         "" if key in PACKER_ASSETS else "build it yourself and set RSVIBE_PACKER")
    sys.exit(1 if bad else 0)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor").set_defaults(fn=cmd_doctor)
    p = sub.add_parser("list"); p.add_argument("url"); p.add_argument("work"); p.set_defaults(fn=cmd_list)
    p = sub.add_parser("fetch"); p.add_argument("work"); p.add_argument("--jobs", type=int, default=3); p.set_defaults(fn=cmd_fetch)
    p = sub.add_parser("check"); p.add_argument("work"); p.set_defaults(fn=cmd_check)
    p = sub.add_parser("pack"); p.add_argument("work"); p.add_argument("game", nargs="?")
    p.set_defaults(fn=cmd_pack)
    argv = sys.argv[1:]
    cut = argv.index("--") if "--" in argv else len(argv)  # everything after -- goes to the packer untouched
    a = ap.parse_args(argv[:cut])
    a.extra = argv[cut + 1:]
    try:
        a.fn(a)
    except KeyboardInterrupt:
        sys.exit("interrupted; run the same command again to resume")
    except BrokenPipeError:  # output piped into head/less that closed early
        os._exit(0)


if __name__ == "__main__":
    main()
