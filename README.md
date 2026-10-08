# ReSkate Vibe

An AI agent skill that turns a public music playlist from any platform into a packed
[ReSkate](https://github.com/Dingo-Shenanigans/ReSkate) playlist mod for **skate.**

Give your agent a playlist link. It lists the tracks, cleans up their names, downloads and tags the
audio, and packs everything into an add-only music mod: a new radio playlist in the game's music menu.
Your stock soundtrack stays untouched.

```
"Turn https://open.spotify.com/playlist/... into a ReSkate mod"
```

## Supported platforms

| Platform | How the tracklist is read |
|---|---|
| Spotify (playlists, albums) | Public embed page, no login needed (about the first 100 tracks) |
| Apple Music | Public playlist page |
| Deezer | Public API |
| YouTube, YouTube Music, SoundCloud, Bandcamp, ... | `yt-dlp` |
| Anything else (Tidal, Amazon Music, a pasted list, a screenshot) | The agent reads it and writes `tracks.json` itself |

The playlist must be public.

## Requirements

- `python3`, [`yt-dlp`](https://github.com/yt-dlp/yt-dlp), and `ffmpeg` / `ffprobe` on your `PATH`
- skate. with [ReSkate](https://github.com/Dingo-Shenanigans/ReSkate) installed. The packer reads
  template assets from the game folder, and the runtime must be new enough to read `reskate-music.json`.
- [ReSkateMusicPacker, cross-platform fork](https://github.com/ValveTextureFile/ReSkateMusicPacker/releases/tag/v1.1.2-crossplatform.3).
  `rsvibe.py pack` downloads it and verifies its SHA-256. Native builds exist for Windows, macOS and Linux (x86-64).

## Install

The skill is the folder `.claude/skills/reskate-vibe/`: a `SKILL.md` with instructions plus one script.

**Claude Code:** clone this repo and open it, or copy the folder into `~/.claude/skills/` to use it
everywhere. Then ask for a playlist mod, or run `/reskate-vibe`.

**Agents that support the `SKILL.md` format:** copy `reskate-vibe/` into that agent's skills folder.

**Any other agent** (Codex, Gemini CLI, Cursor, Aider, ...): the instructions are plain Markdown.
Point the agent at the file:

```
Follow .claude/skills/reskate-vibe/SKILL.md to turn <playlist url> into a ReSkate mod.
```

You can also put that line in the agent's project instructions file (`AGENTS.md`, `GEMINI.md`, ...).

## Running the steps yourself

The script works without an agent. The name clean-up step is the part that needs judgment, so you do
it by hand:

```bash
S=.claude/skills/reskate-vibe/scripts/rsvibe.py
python3 $S doctor                          # tools, game folder, packer support
python3 $S list "<playlist url>" mymix     # -> mymix/tracks.json
# edit mymix/tracks.json: fix artist/title where needed
python3 $S fetch mymix                     # -> mymix/songs/*.mka, tagged; re-run to resume
python3 $S pack mymix                      # -> mymix/mod (game folder auto-detected, or pass it)
python3 $S pack mymix -- --thunderstore --author You   # extra args after -- go to the packer
```

Each command explains what went wrong and how to fix it. `python3 $S --help` lists everything.

To install the mod, copy `mymix/mod/` into `Mods/` beside `Skate.exe` (or use the ReSkate launcher's
**MODS** page), then restart the game. In game: Radio on the interaction wheel → radio playlists →
your playlist.

## How it works

1. **list**: reads the playlist into `tracks.json`
   (`{"name", "url", "tracks": [{"artist", "title", "duration"?, "source"?}]}`).
2. **clean**: the agent fixes names. The in-game song id is exactly `Artist - Title`, and it must be
   unique the way the packer compares ids: ignoring case and with smart quotes folded to plain ones.
3. **fetch**: downloads each track. It uses the `source` URL if there is one. Otherwise it searches
   YouTube Music's song results, then YouTube, and picks the result whose length matches the original.
   The audio is copied losslessly into tagged `.mka` files, with a `001` prefix that keeps the playlist
   order. Each run reconciles `songs/` with `tracks.json` (renumbers, re-tags, removes extra files) and
   writes `report.json`.
4. **check**: makes sure `songs/` matches `tracks.json` exactly before packing.
5. **pack**: runs ReSkateMusicPacker, which encodes the audio to Opus, levels it to the game's
   loudness and writes the mod.

## Repo layout

```
.claude/skills/reskate-vibe/
  SKILL.md            # the agent instructions (the skill itself)
  scripts/rsvibe.py        # doctor / list / fetch / check / pack
  scripts/test_rsvibe.py   # offline self-test
```

## Contributing

Contributions are welcome. So is work done with any AI agent: Claude, Codex, Gemini, Cursor, Copilot,
or by hand. Good places to start:

- **New platform readers.** Add a function to `rsvibe.py` that takes a URL and returns
  `(playlist_name, [{"artist", "title", "duration"?}])` (duration in seconds, if the site gives it), then
  route its domain in `cmd_list`. Prefer public pages or
  APIs that need no login or API key.
- **Better matching.** Search results sometimes pick a cover, a live version or a remix. Smarter
  search queries or duration checks are welcome.
- **Agent support.** If your agent needs a different file or format to discover the skill, add it and
  document it under [Install](#install). Keep `SKILL.md` as the one source of truth: other files
  should point to it, not copy it.
- **Clearer instructions.** If an agent gets a step wrong, the fix usually belongs in `SKILL.md`.

Before you open a PR:

- Run `python3 .claude/skills/reskate-vibe/scripts/test_rsvibe.py` (offline, prints `all passed`).
- Test it against a real public playlist. Run `list`, then `fetch` and `check` on a couple of tracks,
  and say in the PR which agent (if any) you used.
- Keep `rsvibe.py` free of third-party packages: standard library, plus `yt-dlp` and `ffmpeg` as
  external tools.
- Never commit downloaded audio, built mods, cookies or API keys.

## Legal

This tool downloads and repackages copyrighted recordings. Use the mods you build for yourself.
Publishing one (for example on Thunderstore) means redistributing other people's music, so make sure
you have the rights first. This project is not affiliated with EA, skate., Spotify, Apple, Google or
Deezer.

## Credits

- [ReSkate](https://github.com/Dingo-Shenanigans/ReSkate): the skate. modding runtime
- [ReSkateMusicPacker](https://github.com/DeckardDetribine/ReSkateMusicPacker) by DeckardDetribine,
  and its [cross-platform fork](https://github.com/ValveTextureFile/ReSkateMusicPacker/tree/cross-platform)
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) and [FFmpeg](https://ffmpeg.org/)
