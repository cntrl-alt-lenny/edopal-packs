"""Build EDOPal pack archives and the local development catalog.

Run `python tools/build_catalog.py` from the packs checkout (or invoke by absolute path).
For releases, pass `--release-base https://github.com/OWNER/REPO/releases/download/TAG`.
The ZIPs in dist/ are the release assets. Thumbnails and previews are served from the
raw packs repository. Override its URL with --media-base when using a fork or mirror.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import stat
import sys
import unicodedata
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from urllib.parse import quote, urlsplit
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
MEDIA_BASE = "https://raw.githubusercontent.com/cntrl-alt-lenny/edopal-packs/main"
COLLECTIONS = {
    "theme": "themes",
    "cardback": "cardbacks",
    "music": "music",
    "format": "formats",
    "art": "art",
    "icons": "icons",
}
GROUPS = {"tcg-ocg", "anime", "manga", "video-games", "other-tcgs"}
ROLES = {"menu", "deck", "duel", "win", "lose"}
SCENES = ("menu", "deck", "duel")
INVALID_PATH_CHAR = re.compile(r'[<>:"\\|?*\x00-\x1f]')
WINDOWS_DEVICE = re.compile(r"(?:CON(?:IN\$|OUT\$)?|PRN|AUX|NUL|(?:COM|LPT)[1-9¹²³])", re.I)


def field(obj: dict, key: str, expected: type):
    value = obj.get(key)
    if type(value) is not expected or (expected is str and not value.strip()):
        raise ValueError(f"{key!r} must be a {expected.__name__} with a value")
    return value


def objects(items: list, label: str):
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"{label}[{index}] must be an object")
        yield item


def strings(items: list, label: str) -> None:
    if not all(isinstance(item, str) for item in items):
        raise ValueError(f"{label!r} must contain strings")


def safe_relative(name: str) -> str:
    path = PurePosixPath(name)
    if (
        not path.parts
        or path.is_absolute()
        or str(path) != name
        or ".." in path.parts
        or INVALID_PATH_CHAR.search(name)
        or any(part.endswith((" ", ".")) or WINDOWS_DEVICE.fullmatch(part.split(".", 1)[0].rstrip()) for part in path.parts)
    ):
        raise ValueError(f"invalid relative path {name!r}")
    return name


def inventory(source: Path) -> dict:
    files = {}
    seen = {}
    for path in sorted(source.rglob("*")):
        name = safe_relative(path.relative_to(source).as_posix())
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise ValueError(f"symbolic link in pack: {name}")
        key = unicodedata.normalize("NFD", name).casefold()
        if key in seen:
            raise ValueError(f"paths differ only by case: {seen[key]!r} and {name!r}")
        seen[key] = name
        if stat.S_ISDIR(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"expected regular file: {name}")
        if name != "manifest.json":
            files[name] = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    return files


def asset(obj: dict, key: str, files: set[str]) -> str:
    name = safe_relative(field(obj, key, str))
    if name not in files:
        raise ValueError(f"{key!r} references missing file {name!r}")
    return name


def theme_meta(m: dict, files: set[str]) -> dict:
    field(m, "tested_edopro", str)
    skin = safe_relative(field(m, "skin_dir", str))
    if f"{skin}/skin.xml" not in files:
        raise ValueError(f"skin_dir must contain skin.xml: {skin!r}")
    previews = field(m, "previews", dict)
    for key in (*SCENES, "tile"):
        asset(previews, key, files)
    if "font" in m:
        font = field(m, "font", dict)
        asset(font, "text", files)
        asset(font, "num", files)
        if field(font, "size", int) <= 0:
            raise ValueError("font.size must be positive")
    if "default_cardbacks" in m:
        for key, value in field(m, "default_cardbacks", dict).items():
            if key not in ("you", "opponent") or not isinstance(value, str) or not value.startswith("cardback-"):
                raise ValueError(f"invalid default_cardbacks entry {key!r}")
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", field(m, "accent", str)):
        raise ValueError("accent must be a six-digit hex colour beginning with #")
    return {"accent": m["accent"], "tagline": field(m, "tagline", str)}


def cardback_meta(m: dict, files: set[str]) -> dict:
    asset(m, "image", files)
    if "master" in m:
        asset(m, "master", files)
    meta = {k: field(m, k, str) for k in ("label", "era", "group")}
    if meta["group"] not in GROUPS:
        raise ValueError(f"unknown cardback group {meta['group']!r}")
    if "default_for" in m:
        if m["default_for"] not in ("you", "opponent"):
            raise ValueError("default_for must be you or opponent")
        meta["default_for"] = m["default_for"]
    return meta


def music_meta(m: dict, files: set[str]) -> dict:
    tracks = field(m, "tracks", list)
    meta = {"game": field(m, "game", str), "year": field(m, "year", int), "platform": field(m, "platform", str)}
    if "series" in m:
        meta["series"] = field(m, "series", str)
    asset(m, "cover", files)
    duration = 0
    seen = set()
    for track in objects(tracks, "tracks"):
        track_id = field(track, "id", str)
        if track_id in seen:
            raise ValueError(f"duplicate track id {track_id!r}")
        seen.add(track_id)
        field(track, "title", str)
        asset(track, "path", files)
        seconds = track.get("duration_seconds")
        if type(seconds) not in (int, float) or not 0 <= seconds < float("inf"):
            raise ValueError(f"{track_id}.duration_seconds must be a finite nonnegative number")
        duration += seconds
        roles = field(track, "suggested_roles", list)
        strings(roles, "suggested_roles")
        if set(roles) - ROLES:
            raise ValueError(f"{track_id}.suggested_roles includes an unknown music role")
    return {**meta, "tracks": tracks, "track_count": len(tracks), "duration_seconds": round(duration, 3)}


def format_meta(m: dict, files: set[str]) -> dict:
    formats = field(m, "formats", list)
    seen = set()
    for record in objects(formats, "formats"):
        format_id = field(record, "id", str)
        if format_id in seen:
            raise ValueError(f"duplicate format id {format_id!r}")
        seen.add(format_id)
        for key in ("name", "date", "banlist"):
            field(record, key, str)
        strings(field(record, "known_gaps", list), "known_gaps")
        # Current lists can refer to EDOPro's built-in banlists. Points-based formats
        # can appear in the library with an explicit unplayable status and no recipe.
        unplayable = record.get("playable") is False or record.get("status") == "unsupported"
        stock = record.get("status") == "stock" or bool(record.get("stock_lflist"))
        if not unplayable and not stock:
            field(record, "hash", str)
            asset(record, "lflist", files)
        if unplayable and not record.get("host"):
            continue
        host = field(record, "host", dict)
        field(host, "rule", str)
        strings(field(host, "options", list), "host.options")
        for key in ("lp", "hand", "draw"):
            field(host, key, int)
    return {"formats": len(formats)}


def metadata(m: dict, kind: str, expected_id: str, files: set[str]) -> dict:
    if field(m, "schema", int) != 1:
        raise ValueError("expected manifest schema 1")
    if field(m, "id", str) != expected_id or not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", expected_id):
        raise ValueError(f"expected a lowercase, hyphenated pack id matching its folder: {expected_id!r}")
    if field(m, "kind", str) != kind:
        raise ValueError(f"expected kind {kind!r}")
    field(m, "name", str)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+_-]*", field(m, "version", str)):
        raise ValueError("version must be safe to include in a ZIP filename")
    if "files" in m:
        field(m, "files", dict)  # This generated field can be absent or stale in authored manifests.
    credits = [] if kind == "icons" and "credits" not in m else field(m, "credits", list)
    for credit in objects(credits, "credits"):
        field(credit, "what", str)
        field(credit, "source", str)
        if not isinstance(credit.get("url"), str):
            raise ValueError("credit.url must be a string")
    validators = {"theme": theme_meta, "cardback": cardback_meta, "music": music_meta, "format": format_meta}
    validator = validators.get(kind)
    return validator(m, files) if validator else {}


def pack_sources(root: Path):
    for kind, dirname in COLLECTIONS.items():
        folder = root / dirname
        if folder.is_symlink():
            raise ValueError(f"pack collection cannot be a symbolic link: {folder}")
        manifests = [folder / "manifest.json"] if kind == "format" else sorted(folder.glob("*/manifest.json"))
        for path in manifests:
            if path.parent.is_symlink() or path.is_symlink():
                raise ValueError(f"pack source cannot be a symbolic link: {path}")
            if path.is_file():
                yield path, kind, "formats" if kind == "format" else f"{kind}-{path.parent.name}"


def json_bytes(obj: dict) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()


def unique_object(pairs: list[tuple]) -> dict:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("duplicate field in manifest JSON")
    return result


def zip_entry(name: str) -> ZipInfo:
    info = ZipInfo(name)  # Fixed DOS epoch, UNIX creator and permissions for reproducible archives.
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    info.compress_type = ZIP_DEFLATED
    return info


def write_zip(path: Path, source: Path, manifest: dict, names: list[str], original: bytes) -> bytes:
    hashes = {}
    with ZipFile(path, "w") as archive:
        for name in names:
            digest = hashlib.sha256()
            with (source / name).open("rb") as reader, archive.open(zip_entry(name), "w") as writer:
                while chunk := reader.read(1024 * 1024):
                    digest.update(chunk)
                    writer.write(chunk)
            hashes[name] = digest.hexdigest()
        if manifest.get("files") == hashes:
            encoded = original
        else:
            manifest["files"] = hashes
            encoded = json_bytes(manifest)
        archive.writestr(zip_entry("manifest.json"), encoded)
    return encoded


def url(base: str, path: str) -> str:
    return f"{base.rstrip('/')}/{quote(path)}" if base and path else path


def public_media(root: Path, source: Path, m: dict, meta: dict, media_base: str) -> tuple[str, str]:
    pack_id = m["id"]

    def media(rel: str, fallback: str = "") -> str:
        path = root / rel
        if path.parent.is_symlink() or path.is_symlink():
            raise ValueError(f"public media cannot be a symbolic link: {rel}")
        if path.is_file():
            return url(media_base, rel)
        if fallback:
            return url(media_base, (source / fallback).relative_to(root).as_posix())
        return ""

    thumbnail = media(f"thumbs/{pack_id}.webp", m["previews"]["tile"] if m["kind"] == "theme" else "")
    preview = ""
    if m["kind"] == "theme":
        meta["previews"] = [media(f"previews/{pack_id}-{scene}.webp", m["previews"][scene]) for scene in SCENES]
        preview = meta["previews"][0]
    elif m["kind"] == "music":
        for extension in ("ogg", "wav", "mp3", "flac"):
            relative = f"previews/{pack_id}.{extension}"
            if preview := media(relative):
                if m["tracks"]:
                    meta["preview_track"] = m["tracks"][0]["id"]
                with (root / relative).open("rb") as reader:
                    meta["preview_sha256"] = hashlib.file_digest(reader, "sha256").hexdigest()
                break
    elif m["kind"] == "cardback":
        preview = media(f"previews/{pack_id}.webp")
    return thumbnail, preview


def build(root: Path, release_base: str = "", media_base: str = MEDIA_BASE) -> list[dict]:
    root = root.resolve(strict=True)
    for base in (release_base, media_base):
        parsed = urlsplit(base)
        if base and (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(f"expected an absolute HTTPS base URL: {base!r}")
    dist = root / "dist"
    if dist.is_symlink():
        raise ValueError(f"output directory cannot be a symbolic link: {dist}")
    dist.mkdir(exist_ok=True)
    rows = []
    publications = []
    with TemporaryDirectory(dir=dist, prefix=".catalog-") as staging:
        stage = Path(staging)
        for path, kind, pack_id in pack_sources(root):
            source = path.parent
            try:
                original = path.read_bytes()
                manifest = json.loads(original.decode("utf-8-sig"), object_pairs_hook=unique_object)
                if not isinstance(manifest, dict):
                    raise ValueError("manifest root must be an object")
                files = inventory(source)
                meta = metadata(manifest, kind, pack_id, set(files))
                archive = stage / f"{pack_id}-{manifest['version']}.zip"
                encoded = write_zip(archive, source, manifest, sorted(files), original)
                thumbnail, preview = public_media(root, source, manifest, meta, media_base if release_base else "")
                with archive.open("rb") as reader:
                    checksum = hashlib.file_digest(reader, "sha256").hexdigest()
                rows.append(
                    {
                        "id": pack_id,
                        "kind": kind,
                        "name": manifest["name"],
                        "version": manifest["version"],
                        "url": url(release_base, archive.name) if release_base else source.relative_to(root).as_posix(),
                        "size": archive.stat().st_size,
                        "sha256": checksum,
                        "thumbnail": thumbnail,
                        "preview": preview,
                        "meta": meta,
                    }
                )
                staged_manifest = stage / f"{pack_id}.json"
                staged_manifest.write_bytes(encoded)
                publications.append((path, original, files, staged_manifest, archive))
            except (ValueError, OSError) as exc:
                raise ValueError(f"{path.relative_to(root)}: {exc}") from exc
        catalog = stage / "catalog.json"
        kind_order = list(COLLECTIONS)
        rows.sort(
            key=lambda row: (
                kind_order.index(row["kind"]),
                row["kind"] == "music" and not row["meta"]["track_count"],
                row["name"].casefold(),
                row["id"],
            )
        )
        generated = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
        catalog.write_bytes(json_bytes({"schema": 1, "generated": generated, "packs": rows}))
        for path, original, files, _, _ in publications:
            if path.read_bytes() != original or inventory(path.parent) != files:
                raise ValueError(f"pack source changed during build; rerun: {path.parent.relative_to(root)}")
        for path, original, _, staged_manifest, archive in publications:
            if staged_manifest.read_bytes() != original:
                shutil.copymode(path, staged_manifest)
                staged_manifest.replace(path)
            archive.replace(dist / archive.name)
        catalog.replace(root / "catalog.json")
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=ROOT, help="packs checkout (default: parent of tools)")
    parser.add_argument("--release-base", default="", help="base URL of release ZIP assets (default: local directory URLs)")
    parser.add_argument("--media-base", default=MEDIA_BASE, help="base URL for the raw packs tree in release catalogs")
    args = parser.parse_args()
    try:
        rows = build(args.root, args.release_base, args.media_base)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"{parser.prog}: {exc}\n")
    print(f"Built {len(rows)} packs in {args.root.resolve() / 'dist'} and wrote catalog.json.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
