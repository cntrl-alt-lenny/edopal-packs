"""Export the four art packs from the approved design assets and local card scans.

Run from the app checkout with Pillow installed. After export, run build_catalog.py
to generate the archive sizes, SHA-256s, and local catalog.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

from build_catalog import ROOT, inventory, json_bytes
from PIL import Image, ImageOps

DESIGN = ROOT.parent / "_reference" / "design"
PROFILES = ROOT / "formats" / "profiles"
RESAMPLE = Image.Resampling.LANCZOS
# The approved format art crops use this interior rectangle on 813 × 1185 scans.
ART_BOX = (98, 217, 716, 835)
HERO_FOCUS = {
    "adamancipator": 0.12,
    "burning-abyss": 0.12,
    "frog": 0.12,
    "london": 0.12,
    "shaddoll": 0.12,
    "sky-striker": 0.12,
}


def load(path: Path):
    return json.loads(path.read_bytes())


def jpeg(image: Image.Image, path: Path, quality: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="JPEG", quality=quality, optimize=True, progressive=True)


def read_image(path: Path) -> Image.Image:
    with Image.open(path) as image:
        return image.convert("RGB")


def credit(what: str, source: str, url: str = "") -> dict:
    return {"what": what, "source": source, "url": url}


def manifest(output: Path, name: str, credits: list[dict], **fields) -> None:
    hashes = {}
    for relative in inventory(output):
        with (output / relative).open("rb") as stream:
            hashes[relative] = hashlib.file_digest(stream, "sha256").hexdigest()
    data = {
        "schema": 1,
        "id": f"art-{output.name}",
        "kind": "art",
        "name": name,
        "version": "1.0.0",
        "credits": credits,
        **fields,
        "files": hashes,
    }
    (output / "manifest.json").write_bytes(json_bytes(data))


def daily(output: Path) -> None:
    source = DESIGN / "bannercards"
    ai = DESIGN / "upscale-ai2" / "cards"
    index = load(source / "banners.json")
    expected = {f"{row['id']}.jpg" for row in index}
    if len(index) != len(expected) or expected != {p.name for p in (source / "crops").glob("*.jpg")}:
        raise ValueError("Card-of-the-Day metadata differs from the crop inventory")
    upscaled = 0
    for name in sorted(expected):
        candidate = ai / name
        image = None
        if candidate.is_file():
            try:
                image = read_image(candidate)
                if image.width * 460 != image.height * 1600:
                    image = None
            except OSError:
                print(f"Skipping unreadable upscale still in progress: {candidate}")
        if image is None:
            image = read_image(source / "crops" / name)
        else:
            upscaled += 1
        jpeg(image.resize((1600, 460), RESAMPLE), output / "banners" / name, 86)
        jpeg(image.resize((800, 230), RESAMPLE), output / "thumbs" / name, 84)
    shutil.copyfile(source / "banners.json", output / "banners.json")
    manifest(
        output,
        "Card of the Day",
        [credit("Yu-Gi-Oh! card illustrations", "Konami / approved EDOPal card banner crops")],
        index="banners.json",
    )
    print(f"Card of the Day: {len(index)} banners, {upscaled} from the upscale directory.")


def music(output: Path) -> None:
    source = DESIGN / "musicart"
    attribution = load(source / "credits.json")
    (output / "logos").mkdir(parents=True)
    for slug, game in sorted(attribution["games"].items()):
        banner = f"banners/{slug}.jpg"
        logo = f"logos/{slug}.png"
        if (game["banner"]["file"], game["logo"]["file"]) != (banner, logo):
            raise ValueError(f"Unexpected music media paths for {slug}")
        image = read_image(source / banner)
        if image.size != (1600, 500):
            raise ValueError(f"Unexpected dimensions for {source / banner}: {image.size}")
        jpeg(image, output / banner, 86)
        with Image.open(source / logo) as image:
            transparent = image.convert("RGBA")
        transparent.thumbnail((640, 320), RESAMPLE)
        transparent.save(output / logo, optimize=True)
    shutil.copyfile(source / "credits.json", output / "credits.json")
    manifest(
        output,
        "Music Banners",
        [credit("Game artwork and logos", "Per-game artwork sources and attribution are listed in credits.json")],
        index="credits.json",
    )


def archetypes(output: Path) -> None:
    source = DESIGN / "archetypes2"
    index = load(source / "archetypes.json")
    expected = {re.sub(r"[^a-z0-9]+", "-", row["name"].lower()).strip("-") + ".jpg" for row in index}
    if len(index) != len(expected):
        raise ValueError("Archetype names have conflicting filenames")
    for folder, quality in (("thumbs", 82), ("wide", 80)):
        paths = sorted((source / folder).glob("*.jpg"))
        if expected != {path.name for path in paths}:
            raise ValueError(f"Archetype metadata differs from the {folder} inventory")
        for path in paths:
            jpeg(read_image(path), output / folder / path.name, quality)
    shutil.copyfile(source / "archetypes.json", output / "archetypes.json")
    manifest(
        output,
        "Archetype Artwork",
        [credit("Yu-Gi-Oh! card illustrations", "Konami / approved EDOPal archetype crops")],
        index="archetypes.json",
    )


def format_heroes(output: Path, pics: Path) -> None:
    source = DESIGN / "upscale" / "formats"
    records = load(PROFILES / "index.json")
    formats = {row["id"] for row in load(PROFILES.parent / "manifest.json")["formats"]}
    if len(records) != len(formats) or formats != {row["id"] for row in records}:
        raise ValueError("Format profile index differs from the format pack inventory")
    prepared = {
        row["id"]: row["card_id"] for row in load(source.parent / "report.json")["images"] if row["category"] == "formats"
    }
    heroes = {}
    for row in records:
        slug, card = row["id"], row["hero_card_id"]
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
            raise ValueError(f"Invalid format ID {slug!r}")
        profile = load(PROFILES / f"{slug}.json")
        if card not in {entry["id"] for entry in profile["defining_cards"]}:
            raise ValueError(f"Hero card {card} is absent from the {slug} defining cards")
        path = source / f"{slug}.jpg"
        if path.is_file() and prepared.get(slug) == card:
            art = read_image(path)
            focus = 0.38
        else:
            scan = read_image(pics / f"{card}.jpg")
            left, top, right, bottom = ART_BOX
            art = scan.crop(
                (
                    round(left * scan.width / 813),
                    round(top * scan.height / 1185),
                    round(right * scan.width / 813),
                    round(bottom * scan.height / 1185),
                )
            )
            focus = HERO_FOCUS.get(slug, 0.28)
        hero = ImageOps.fit(art, (1600, 560), RESAMPLE, centering=(0.5, focus))
        jpeg(hero, output / f"{slug}.jpg", 85)
        heroes[slug] = card
    manifest(
        output,
        "Format Heroes",
        [credit("Yu-Gi-Oh! card illustrations", "Konami / local EDOPro scans and approved EDOPal format artwork")],
        cards=dict(sorted(heroes.items())),
    )


def publish(source: Path, output: Path) -> None:
    """Publish a complete staged pack, restoring the old directory if the move fails."""
    previous = source.with_name(f"{source.name}.previous")
    if output.exists():
        output.rename(previous)
    try:
        source.rename(output)
    except OSError:
        if previous.exists():
            previous.rename(output)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pics",
        type=Path,
        default=Path.home() / "Games" / "ProjectIgnis" / "pics",
        help="EDOPro pics directory (read-only)",
    )
    args = parser.parse_args()
    root = ROOT / "art"
    root.mkdir(exist_ok=True)
    with TemporaryDirectory(dir=root, prefix=".package-art-") as directory:
        stage = Path(directory)
        daily(stage / "card-of-the-day")
        music(stage / "music-banners")
        archetypes(stage / "archetypes")
        format_heroes(stage / "format-heroes", args.pics)
        for source in sorted(stage.iterdir()):
            if (source / "manifest.json").is_file():
                publish(source, root / source.name)
                print(f"Packaged {source.name}")


if __name__ == "__main__":
    main()
