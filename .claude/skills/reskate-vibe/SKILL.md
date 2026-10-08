---
name: reskate-vibe
description: Take any public music playlist on any platform (Spotify, Apple Music, YouTube / YouTube Music, Deezer, SoundCloud, Bandcamp, ...) and turn it into a packed ReSkate playlist mod for skate. Use when the user gives a playlist link and wants it in-game as a ReSkate radio station / music mod.
---

# ReSkate Vibe

Turns a public playlist link into an add-only ReSkate music mod: a new radio playlist in skate.'s
music menu. Songs are added; the game's own music is untouched.

Everything runs through `scripts/rsvibe.py` in this skill's folder. Call it by its full path; it is
written as `rsvibe.py` below. Pick a work folder per playlist, e.g. `./<playlist-slug>/`.

```
doctor → list → (you clean names) → fetch → check → pack → report
```

Every command is safe to re-run, prints what to do on failure, and exits non-zero when something
needs your attention.

## 1. Doctor

```bash
python3 rsvibe.py doctor
```

Checks Python 3.8+, `yt-dlp`, `ffmpeg`, `ffprobe`, the game folder and packer support. `FAIL` lines
must be fixed: tell the user what to install. Don't install system packages yourself. `warn` lines are
advisory. If the game folder isn't found automatically, ask the user for the folder containing `Skate.exe`.

## 2. List the tracks

```bash
python3 rsvibe.py list "<playlist url>" <work>
```

This writes `<work>/tracks.json`. If one already exists it's backed up to `tracks.json.bak`. It reads
Spotify (playlists and albums, including `spotify:` URIs and `spotify.link` short links), Apple Music,
Deezer, and anything `yt-dlp` can list (YouTube, YouTube Music, SoundCloud, Bandcamp, ...).

```json
{"name": "Playlist Name", "url": "...", "tracks": [
  {"artist": "A", "title": "T", "duration": 215.3, "source": "https://... (optional)"}]}
```

`duration` (seconds) is optional. When it's present, `fetch` uses it to pick the right version.

- **Other platforms** (Tidal, Amazon Music, a pasted list, a screenshot): read the tracklist yourself and
  write `tracks.json` in this format.
- **Errors:** an error usually means the playlist is private or the link is wrong. Ask the user.
- **The 100-track warning:** this shows up when a Spotify or Apple Music playlist may be cut off at 100
  tracks. Tell the user and ask them to paste the rest.

## 3. Clean the names (your judgment)

The in-game song id is exactly `Artist - Title`. Read `tracks.json` and fix:

- **Swapped fields:** YouTube uploads often have artist and title the wrong way round, or a channel
  name as the artist. Put the real artist and title in. Common noise like `(Official Video)` is already
  stripped.
- **Keep credits:** leave `feat.` credits and remix/live labels in place. They are part of the song's
  identity.
- **Remove duplicates.**
- **Leave `source` alone,** unless you are fixing a wrong match (step 4).

`fetch`, `check` and `pack` all validate `tracks.json` first. They list every problem (empty, too long,
control characters, duplicate ids, invalid `source`) with the track number, so fix those and re-run.

## 4. Fetch

```bash
python3 rsvibe.py fetch <work> [--jobs 3]
```

This downloads every track into `<work>/songs/NNN Artist - Title.mka`, as lossless copies tagged with
the exact artist/title. The `NNN` prefix sets the playlist order.

- **Matching:** with no `source`, it searches YouTube Music's song results, then YouTube, and keeps
  the candidate whose length matches `duration`.
- **Speed and timeouts:** it takes a few seconds per track. For big playlists, run it with a long
  timeout or in the background. If it gets interrupted or times out, run the same command again: it
  resumes, and finished files are never partial.
- **Editing `tracks.json` afterwards is fine:**
  - Reordered, renamed or retitled tracks are renumbered and re-tagged without downloading again.
  - Removed tracks are deleted from `songs/`.
  - Changing a track's `source` re-downloads that track.

At the end it prints a summary and writes `<work>/report.json` (status `ok`, `mismatch` or `failed` per
track). Exit code 1 means some tracks failed.

- **`FAILED`:** fix the artist/title spelling, or find the right video and set it as `source`, then run
  `fetch` again. If it really is unavailable, remove the track and tell the user.
- **`MISMATCH`:** the length differs from the original, so it may be a live version, cover or extended
  cut. Find a better `source`, or accept it and mention it in your report.
- **"Sign in to confirm you're not a bot":** YouTube is rate-limiting. Ask the user, then re-run with
  `RSVIBE_YTDLP_ARGS="--cookies-from-browser firefox"` (or their browser).

## 5. Check

```bash
python3 rsvibe.py check <work>
```

This confirms `songs/` matches `tracks.json` exactly: every file present, tags correct, audio readable,
nothing extra. `pack` runs the same check itself, so this step is optional.

## 6. Pack

```bash
python3 rsvibe.py pack <work> [game folder] [-- extra packer args]
```

What it does:
- Finds the game automatically (Steam, including extra libraries and Flatpak, or the EA app), or uses
  the folder you pass.
- Downloads ReSkateMusicPacker `v1.1.2-crossplatform.3` for this OS, checks its SHA-256 and caches it.
- Builds `<work>/mod/`, named after `tracks.json`'s `name`, with a generated playlist cover.

Encoding takes a minute or two per 10 songs.

Useful extra arguments after `--`:
- `--thunderstore --author <Name> --version 1.0.0` writes a Thunderstore zip next to `mod/`.
- `--playlist-artwork "<name>" cover.png` uses a real cover instead of the generated one.
- `--bitrate 64-320` sets the Opus bitrate.

If the packer warns that a song id clashes with the stock soundtrack or another installed mod, change
that track's title slightly (for example add ` (Original)`), then run `fetch` and `pack` again.
`RSVIBE_PACKER=/path/to/binary` uses the user's own build. `RSVIBE_GAME` sets the game folder.

`pack` never writes into the game's `Mods/` folder. The user decides when to install.

## 7. Report

Tell the user:
- **Where the mod is:** the `mod/` path, and the number of songs in it.
- **What changed:** every track that was dropped, and every `mismatch` you kept.
- **How to install:** copy `<work>/mod/` into `Mods/` beside `Skate.exe` (or use the ReSkate launcher's
  MODS page), then restart. In game: Radio on the interaction wheel → radio playlists → the playlist
  name. It needs a ReSkate runtime that reads `reskate-music.json`.
- **Personal use:** the songs are copyrighted recordings, so the mod is for their own use. Publishing
  it on Thunderstore means redistributing someone else's music.
