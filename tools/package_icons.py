"""Assemble the approved duel textures from the EDOPal design checkout."""

from pathlib import Path
from shutil import copyfile
from tempfile import TemporaryDirectory

from build_catalog import json_bytes, write_zip
from PIL import Image

PACKS = Path(__file__).resolve().parents[1]
REF = PACKS.parent / "_reference"
SLOTS = ("act", "attack", "chain", "number", "negated", "target", "chaintarget", "equip", "lim", "lpf", "settings")
VARIANTS = ("act", "attack", "number", "negated", "target", "equip", "lim", "lpf")


def assemble(slug, name, files, credits):
    target = PACKS / "icons" / slug
    for rel, source in files.items():
        dest = target / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        copyfile(source, dest)
    manifest = dict(schema=1, id=f"icons-{slug}", kind="icons", name=name, version="1.0.0", credits=credits)
    with TemporaryDirectory() as tmp:
        encoded = write_zip(Path(tmp) / "pack.zip", target, manifest, sorted(files), json_bytes(manifest))
    (target / "manifest.json").write_bytes(encoded)


def main():
    stock = REF / "skin-lab/lab/textures"
    textures = [f"textures/{key}.png" for key in SLOTS]
    files = {rel: stock.parent / rel for rel in textures}
    files["CREDITS.txt"] = stock / "CREDITS.md.txt"
    project = "https://github.com/ProjectIgnis/EDOPro"
    credits = [
        dict(what="Activation", source="NaimSantos (AGPLv3 or later)", url=project),
        dict(what="Settings and field", source="Icematoro (AGPLv3 or later)", url=project),
        dict(what="Duel indicators", source="Argon Sun (GPLv2)", url=project),
        dict(what="Duel field preview", source="EDOPal · Abyss in EDOPro", url=""),
    ]
    with TemporaryDirectory() as tmp:
        field = Path(tmp) / "field.webp"
        with Image.open(REF / "build/themepacks2/capture/raw/abyss-duel.png") as capture:
            capture.crop((550, 164, 1170, 594)).save(field, lossless=True)
        files["previews/field.webp"] = field
        assemble("stock", "EDOPro Default", files, credits)
    for slug in ("minimal", "minimal-gold", "minimal-glass"):
        source = REF / "design/iconpacks2/packs" / slug
        assemble(
            slug,
            slug.replace("-", " ").title(),
            {rel: source / rel for rel in textures},
            [dict(what="Duel icon textures", source="EDOPal · Minimal series", url="")],
        )
    picks = REF / "design/iconpicks/candidates"
    files = {f"candidates/{key}/{c}.png": picks / key / f"{c}.png" for key in VARIANTS for c in "ABCDE"}
    assemble(
        "candidates", "Duel Icon Candidates", files, [dict(what="Alternate duel icon designs", source="EDOPal", url="")]
    )


if __name__ == "__main__":
    main()
