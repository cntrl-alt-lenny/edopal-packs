"""Build a music pack and its gallery assets from local masters."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import wave
from pathlib import Path

import miniaudio
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
AUDIO = {".wav", ".flac", ".ogg", ".mp3"}
ROLES = {"menu", "deck", "duel", "win", "lose"}
DEVICE_NAME = re.compile(r"con|aux|prn|nul|(?:com|lpt)[1-9]")
TARGET = "loudnorm=I=-16:TP=-1:LRA=11"


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def track_list(source: Path) -> list[tuple[Path, dict]]:
    inputs = sorted(
        (p for p in source.iterdir() if p.is_file() and p.suffix.lower() in AUDIO), key=lambda p: p.name.casefold()
    )
    metadata_path = source / "tracks.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else {}
    if not isinstance(metadata, dict) or metadata.keys() - {p.name for p in inputs}:
        raise ValueError("tracks.json must map source audio filenames to their metadata")
    if not inputs:
        raise ValueError("No WAV, FLAC, Ogg or MP3 audio in the source folder")
    tracks = []
    ids = set()
    # With metadata, preserve the author's sequence. Append any unlisted files in filename order.
    ordered = [source / name for name in metadata] + [p for p in inputs if p.name not in metadata]
    for number, path in enumerate(ordered, start=1):
        entry = metadata.get(path.name, {})
        if not isinstance(entry, dict):
            raise ValueError(f"Invalid metadata for {path.name}")
        text = unicodedata.normalize("NFKD", path.stem).encode("ascii", "ignore").decode()
        slug = re.sub("[^a-z0-9]+", "-", text.lower()).strip("-")
        if not slug or DEVICE_NAME.fullmatch(slug):
            slug = f"track-{number}"
        if len(slug) > 96:
            raise ValueError(f"Track identifier exceeds 96 characters. Shorten the filename {path.name!r}.")
        if slug in ids:
            raise ValueError(f"Multiple filenames produce track id {slug!r}. Rename one source file.")
        ids.add(slug)
        roles = entry.get("suggested_roles", [])
        if not isinstance(roles, list) or any(not isinstance(r, str) or r not in ROLES for r in roles):
            raise ValueError(f"Invalid suggested_roles for {path.name}")
        title = entry.get("title", path.stem.replace("_", " "))
        if not isinstance(title, str) or not title.strip():
            raise ValueError(f"Invalid title for {path.name}")
        tracks.append((path, {"id": slug, "title": title, "suggested_roles": roles}))
    return tracks


def cover_assets(source: Path, cover: Path, thumb: Path) -> None:
    with Image.open(source) as im:
        art = ImageOps.exif_transpose(im).convert("RGB")
        art.thumbnail((900, 900), Image.Resampling.LANCZOS)
        art.save(cover, "WEBP", quality=90, method=6)
        art.thumbnail((480, 480), Image.Resampling.LANCZOS)
        for quality in range(86, 5, -8):
            art.save(thumb, "WEBP", quality=quality, method=6)
            if thumb.stat().st_size < 60_000:
                break


def ffmpeg(executable: str, source: Path, *args: str) -> str:
    proc = subprocess.run(
        [executable, "-hide_banner", "-nostats", "-nostdin", "-i", str(source), "-vn", "-sn", "-dn", "-map", "0:a:0", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode:
        raise ValueError(f"FFmpeg failed on {source.name}: {proc.stderr.strip()[-1200:]}")
    return proc.stderr


def loudness_filter(executable: str, path: Path) -> str:
    log = ffmpeg(executable, path, "-af", f"{TARGET}:print_format=json", "-f", "null", "-")
    values = json.loads(log[log.rindex("{") : log.rindex("}") + 1])
    fields = {
        "measured_I": "input_i",
        "measured_TP": "input_tp",
        "measured_LRA": "input_lra",
        "measured_thresh": "input_thresh",
    }
    # Very short cues and silent masters can be below the loudness measurement gate.
    if not all(math.isfinite(float(values[key])) for key in [*fields.values(), "target_offset"]):
        return "anull"
    return ":".join(
        [
            TARGET,
            *(f"{option}={values[key]}" for option, key in fields.items()),
            "linear=true",
            f"offset={values['target_offset']}",
        ]
    )


def encode(executable: str, source: Path, dest: Path, loudness: str, *, preview: bool = False) -> None:
    limit = ["-t", "20"] if preview else []
    ffmpeg(
        executable,
        source,
        *limit,
        "-af",
        loudness,
        "-ac",
        "2",
        "-ar",
        "44100",
        "-c:a",
        "libvorbis",
        "-b:a",
        "96k" if preview else "160k",
        "-map_metadata",
        "-1",
        str(dest),
    )


def wav_preview(source: Path, dest: Path) -> None:
    rate = 22050
    remaining = 20 * rate
    stream = miniaudio.stream_file(str(source), nchannels=1, sample_rate=rate)
    with wave.open(str(dest), "wb") as out:
        out.setparams((1, 2, rate, 0, "NONE", ""))
        try:
            while remaining:
                block = next(stream, None)
                if not block:
                    break
                block = block[:remaining]
                if sys.byteorder == "big":
                    block.byteswap()
                out.writeframesraw(block.tobytes())
                remaining -= len(block)
        finally:
            stream.close()


def build(
    source: Path,
    root: Path,
    *,
    slug: str,
    name: str,
    game: str,
    year: int,
    platform: str,
    cover: Path,
    credits: list[dict],
    version: str = "1.0.0",
) -> Path:
    if len(slug) > 90 or not re.fullmatch("[a-z0-9]+(?:-[a-z0-9]+)*", slug) or DEVICE_NAME.fullmatch(slug):
        raise ValueError("Use a portable lowercase pack slug such as sample-afterglow")
    tracks = track_list(source)
    pack_id = "music-" + slug
    executable = shutil.which("ffmpeg")
    folder = root / "music" / slug
    thumb = root / "thumbs" / f"{pack_id}.webp"
    preview_base = root / "previews" / pack_id
    clip = preview_base.with_suffix(".ogg" if executable else ".wav")
    if any(p.exists() for p in (folder, thumb, preview_base.with_suffix(".ogg"), preview_base.with_suffix(".wav"))):
        raise ValueError(f"Output already exists for {pack_id}. Choose an empty staging root to rebuild it.")
    for directory in (folder.parent, thumb.parent, clip.parent):
        directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".build-music-", dir=root) as tmp:
        stage = Path(tmp)
        pack = stage / slug
        (pack / "audio").mkdir(parents=True)
        cover_assets(cover, pack / "cover.webp", stage / thumb.name)
        for index, (input_path, track) in enumerate(tracks):
            ext = ".ogg" if executable else input_path.suffix.lower()
            path = pack / "audio" / (track["id"] + ext)
            if executable:
                gain = loudness_filter(executable, input_path)
                encode(executable, input_path, path, gain)
                if index == 0:
                    encode(executable, input_path, stage / clip.name, gain, preview=True)
            else:
                shutil.copyfile(input_path, path)
                if index == 0:
                    wav_preview(input_path, stage / clip.name)
            track["duration_seconds"] = round(miniaudio.get_file_info(str(path)).duration, 3)
            track["path"] = path.relative_to(pack).as_posix()
        manifest = {
            "schema": 1,
            "id": pack_id,
            "kind": "music",
            "name": name,
            "version": version,
            "game": game,
            "year": year,
            "platform": platform,
            "cover": "cover.webp",
            "tracks": [track for _, track in tracks],
            "credits": credits,
            "files": {p.relative_to(pack).as_posix(): digest(p) for p in sorted(pack.rglob("*")) if p.is_file()},
        }
        write_json(pack / "manifest.json", manifest)
        published = []
        try:
            for src, dest in ((stage / thumb.name, thumb), (stage / clip.name, clip)):
                with dest.open("xb") as out:
                    published.append(dest)
                    with src.open("rb") as source_file:
                        shutil.copyfileobj(source_file, out)
            pack.rename(folder)
        except OSError:
            for path in published:
                path.unlink(missing_ok=True)
            raise
    return folder


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Folder of local audio masters")
    for opt in ("slug", "name", "game", "platform", "credit"):
        parser.add_argument("--" + opt, required=True)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--cover", type=Path, required=True)
    parser.add_argument("--credit-url", default="")
    parser.add_argument("--version", default="1.0.0")
    parser.add_argument("--root", type=Path, default=ROOT, help="Packs checkout or empty staging directory")
    args = parser.parse_args()
    try:
        build(
            args.source,
            args.root,
            slug=args.slug,
            name=args.name,
            game=args.game,
            year=args.year,
            platform=args.platform,
            cover=args.cover,
            credits=[{"what": "Audio and cover", "source": args.credit, "url": args.credit_url}],
            version=args.version,
        )
    except (OSError, ValueError, miniaudio.DecodeError) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
