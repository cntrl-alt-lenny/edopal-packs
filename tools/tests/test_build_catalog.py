import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile

import pytest

BUILDER = Path(__file__).resolve().parents[1] / "build_catalog.py"


def run_build(root, *args, check=True):
    return subprocess.run(
        [sys.executable, str(BUILDER), "--root", str(root), *args], capture_output=True, text=True, check=check
    )


def put(root, name, data=b"fixture"):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def pack(root, relative, kind, pack_id, fields, payload):
    source = root / relative
    for name in payload:
        put(source, name)
    manifest = {
        "schema": 1,
        "kind": kind,
        "id": pack_id,
        "name": pack_id,
        "version": "1.0",
        "credits": [{"what": "Fixture", "source": "Tests", "url": ""}],
        **fields,
    }
    return put(source, "manifest.json", json.dumps(manifest).encode())


def cardback(root):
    return pack(
        root,
        "cardbacks/test",
        "cardback",
        "cardback-test",
        {
            "image": "back.png",
            "master": "master.png",
            "label": "Test card back",
            "era": "2000",
            "group": "tcg-ocg",
            "default_for": "you",
            "files": {"obsolete.png": "stale"},
        },
        ["back.png", "master.png"],
    )


def test_four_pack_kinds_reproducible_archives_and_urls(tmp_path):
    scenes = {s: f"previews/{s}.webp" for s in ("menu", "deck", "duel", "tile")}
    pack(
        tmp_path,
        "themes/test",
        "theme",
        "theme-test",
        {
            "skin_dir": "skin",
            "previews": scenes,
            "tagline": "Gallery",
            "accent": "#abcdef",
            "tested_edopro": "41.0.2",
            "font": {"text": "fonts/face.ttf", "size": 12, "num": "fonts/face.ttf"},
        },
        ["skin/skin.xml", "fonts/face.ttf", "fonts/OFL.txt", *scenes.values()],
    )
    for scene in ("menu", "deck", "duel"):
        put(tmp_path, f"previews/theme-test-{scene}.webp")
    put(tmp_path, "thumbs/theme-test.webp")
    cardback(tmp_path)
    pack(
        tmp_path,
        "music/test",
        "music",
        "music-test",
        {
            "game": "Game",
            "year": 2004,
            "platform": "PC",
            "cover": "cover.webp",
            "tracks": [
                {
                    "id": "one",
                    "title": "Song",
                    "path": "audio/one.ogg",
                    "duration_seconds": 2.5,
                    "suggested_roles": ["duel", "menu"],
                }
            ],
        },
        ["cover.webp", "audio/one.ogg"],
    )
    put(tmp_path, "previews/music-test.wav")
    pack(
        tmp_path,
        "formats",
        "format",
        "formats",
        {
            "formats": [
                {
                    "id": "hat",
                    "name": "HAT",
                    "date": "2014-07-12",
                    "banlist": "April 2014",
                    "hash": "0x23f83669",
                    "lflist": "lflists/hat.conf",
                    "host": {"rule": "Master Rule 2", "lp": 8000, "hand": 5, "draw": 1, "options": []},
                    "known_gaps": [],
                },
                {
                    "id": "advanced",
                    "name": "Advanced",
                    "status": "stock",
                    "date": "2026-11-01",
                    "banlist": "TCG",
                    "stock_lflist": "lflists/TCG.lflist.conf",
                    "known_gaps": [],
                    "host": {"rule": "Master Rule 5", "lp": 8000, "hand": 5, "draw": 1, "options": []},
                },
                {
                    "id": "genesys",
                    "name": "Genesys",
                    "date": "2026-11-01",
                    "banlist": "Points",
                    "playable": False,
                    "known_gaps": ["Stock EDOPro cannot enforce points."],
                },
            ]
        },
        ["lflists/hat.conf"],
    )

    run_build(tmp_path)
    catalog = json.loads((tmp_path / "catalog.json").read_bytes())
    assert catalog["schema"] == 1
    rows = {p["kind"]: p for p in catalog["packs"]}
    assert set(rows) == {"theme", "format", "cardback", "music"}
    assert rows["theme"]["thumbnail"] == "thumbs/theme-test.webp"
    assert rows["theme"]["meta"]["previews"] == [f"previews/theme-test-{s}.webp" for s in ("menu", "deck", "duel")]
    assert rows["cardback"]["meta"]["default_for"] == "you"
    assert rows["format"]["url"] == "formats" and rows["format"]["meta"]["formats"] == 3
    assert rows["music"]["meta"] == {
        "game": "Game",
        "platform": "PC",
        "year": 2004,
        "track_count": 1,
        "duration_seconds": 2.5,
        "tracks": json.loads((tmp_path / "music/test/manifest.json").read_bytes())["tracks"],
        "preview_track": "one",
        "preview_sha256": hashlib.sha256(b"fixture").hexdigest(),
    }
    assert rows["music"]["preview"] == "previews/music-test.wav"
    for row in rows.values():
        folder = tmp_path / row["url"]
        bundle = tmp_path / "dist" / f"{row['id']}-1.0.zip"
        assert bundle.stat().st_size == row["size"]
        assert hashlib.sha256(bundle.read_bytes()).hexdigest() == row["sha256"]
        with ZipFile(bundle) as z:
            assert z.read("manifest.json") == (folder / "manifest.json").read_bytes()
            files = json.loads(z.read("manifest.json"))["files"]
            assert files.keys() == set(z.namelist()) - {"manifest.json"}
            for name, digest in files.items():
                assert z.read(name) == (folder / name).read_bytes()
                assert digest == hashlib.sha256(z.read(name)).hexdigest()
                os.utime(folder / name, (1, 1))
    run_build(tmp_path, "--release-base", "https://example.org/releases/v1/", "--media-base", "https://example.org/raw/")
    release = json.loads((tmp_path / "catalog.json").read_bytes())
    for row in release["packs"]:
        assert row["url"] == f"https://example.org/releases/v1/{row['id']}-1.0.zip"
        assert row["sha256"] == rows[row["kind"]]["sha256"]
    theme = next(p for p in release["packs"] if p["kind"] == "theme")
    assert theme["thumbnail"] == "https://example.org/raw/thumbs/theme-test.webp"
    assert theme["preview"] == theme["meta"]["previews"][0] == "https://example.org/raw/previews/theme-test-menu.webp"


def test_music_stub_keeps_source_manifest_formatting(tmp_path):
    manifest = pack(
        tmp_path,
        "music/future",
        "music",
        "music-future",
        {
            "game": "Future",
            "platform": "PC",
            "year": 1999,
            "cover": "cover.webp",
            "tracks": [],
            "files": {"cover.webp": hashlib.sha256(b"fixture").hexdigest()},
        },
        ["cover.webp"],
    )
    original = manifest.read_bytes()
    run_build(tmp_path)
    assert manifest.read_bytes() == original
    with ZipFile(tmp_path / "dist/music-future-1.0.zip") as z:
        assert z.read("manifest.json") == original
    row = json.loads((tmp_path / "catalog.json").read_bytes())["packs"][0]
    assert row["meta"]["track_count"] == 0
    assert row["preview"] == ""


@pytest.mark.parametrize(
    "change",
    [
        {"schema": True},
        {"id": "cardback-wrong"},
        {"kind": "theme"},
        {"credits": [{}]},
        {"group": "unknown"},
        {"image": "missing.png"},
        {"master": "../outside"},
        {"version": "../1.2"},
        {"default_for": "both"},
    ],
)
def test_invalid_pack_leaves_previous_catalog_and_zip_intact(tmp_path, change):
    manifest = cardback(tmp_path)
    run_build(tmp_path)
    catalog = (tmp_path / "catalog.json").read_bytes()
    archive = tmp_path / "dist/cardback-test-1.0.zip"
    original = archive.read_bytes()
    modified = json.loads(manifest.read_bytes()) | change
    manifest.write_text(json.dumps(modified))
    result = run_build(tmp_path, check=False)
    assert result.returncode == 1
    assert "cardbacks/test/manifest.json:" in result.stderr
    assert (tmp_path / "catalog.json").read_bytes() == catalog
    assert archive.read_bytes() == original
    assert not list((tmp_path / "dist").glob(".catalog-*"))


def test_symlink_rejected(tmp_path):
    manifest = cardback(tmp_path)
    try:
        (manifest.parent / "linked.png").symlink_to(put(tmp_path, "external.png"))
    except OSError as exc:
        pytest.skip(f"symbolic links unavailable: {exc}")
    result = run_build(tmp_path, check=False)
    assert result.returncode == 1 and "symbolic link" in result.stderr
    assert not (tmp_path / "catalog.json").exists()


def test_concurrent_asset_edit_is_detected_before_publication(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(BUILDER.parent))
    import build_catalog

    path = cardback(tmp_path)
    original = path.read_bytes()
    write_zip = build_catalog.write_zip

    def edit_after_zip(*args):
        data = write_zip(*args)
        (path.parent / "back.png").write_bytes(b"concurrent update")
        return data

    monkeypatch.setattr(build_catalog, "write_zip", edit_after_zip)
    with pytest.raises(ValueError, match="changed during build"):
        build_catalog.build(tmp_path)
    assert path.read_bytes() == original
    assert not (tmp_path / "catalog.json").exists()
    assert not list((tmp_path / "dist").glob("*.zip"))
