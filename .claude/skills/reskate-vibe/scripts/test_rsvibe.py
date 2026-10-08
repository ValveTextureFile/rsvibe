"""Offline self-check for rsvibe.py's pure logic: python3 test_rsvibe.py"""
import re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import rsvibe as r

ok = {"name": "Mix", "tracks": [{"artist": "A", "title": "T"}]}
assert r.validate(ok) == []
assert r.validate({"name": "Mix", "tracks": []})
assert r.validate({"name": " Mix", "tracks": ok["tracks"]}), "edge spaces in name"
assert r.validate({"name": "Mix", "tracks": [{"artist": "A", "title": ""}]}), "empty title"
assert r.validate({"name": "Mix", "tracks": [{"artist": "A\nB", "title": "T"}]}), "control char"
assert r.validate({"name": "Mix", "tracks": [{"artist": "A", "title": "x" * 256}]}), "too long"
assert r.validate({"name": "Mix", "tracks": [{"artist": "A", "title": "T", "source": "-o evil"}]}), "source must be a URL"
dup = r.validate({"name": "Mix", "tracks": [{"artist": "A", "title": "Don’t"}, {"artist": "a", "title": "don't"}]})
assert any("same song id" in p for p in dup), "smart quotes + case fold to one id, like the packer"
assert any("same file name" in p for p in r.validate({"name": "M", "tracks": [{"artist": "A", "title": "x/y"}, {"artist": "A", "title": "x:y"}]}))

assert r.tidy("  a \t b\x00 ") == "a b"
assert r.clean_video_title("Song (Official Music Video) [HD]") == "Song"
assert r.clean_video_title("Song (feat. X)") == "Song (feat. X)", "keeps feat credits"
assert r.clean_video_title("Song (Remix)") == "Song (Remix)", "keeps remix names"
assert r.safe('a/b:c?. ') == "a_b_c_"

assert r.close(200, None) and r.close(205, 200) and not r.close(260, 200) and not r.close(None, 200)

for u in ["https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=x", "https://open.spotify.com/intl-de/album/abc123",
          "spotify:playlist:37i9dQZF1DXcBWIGoYBM5M", "https://open.spotify.com/user/bob/playlist/abc"]:
    assert re.search(r.SPOTIFY_RE, u), u
assert r.resolve("open.spotify.com/playlist/x") == "https://open.spotify.com/playlist/x"
print("all passed")
