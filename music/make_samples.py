"""Create the two original sample soundtracks and their illustrated jackets."""

from __future__ import annotations

import argparse
import math
import random
import sys
import tempfile
import wave
from array import array
from functools import lru_cache
from pathlib import Path

from build_pack import ROOT, build, write_json
from PIL import Image, ImageDraw, ImageFont, ImageOps

RATE = 22050
BEATS = 32
TAU = 2 * math.pi

# Four bars, repeated with a change in the upper voice. Notes are MIDI pitches.
AFTERGLOW = ((50, 57, 61, 64), (47, 54, 57, 62), (43, 50, 54, 59), (45, 52, 59, 61))
TIDEGLASS = ((48, 55, 58, 62), (44, 51, 55, 58), (51, 58, 62, 65), (46, 53, 60, 62))
ALBUMS = {
    "afterglow": [
        ("First Light", 96, 0, ["menu"]),
        ("Copper Skies", 108, 5, ["duel"]),
        ("Homeward", 90, -2, ["win"]),
    ],
    "tideglass": [
        ("Tidal Pool", 88, 0, ["deck"]),
        ("Sea of Glass", 98, 3, ["menu", "duel"]),
        ("Low Water", 84, -3, ["lose"]),
    ],
}


@lru_cache(maxsize=96)
def voice(note: int, seconds: float, instrument: str) -> array:
    phase = TAU * 440 * 2 ** ((note - 69) / 12) / RATE
    length = round(seconds * RATE)
    sound = array("f")
    for i in range(length):
        t = i / RATE
        angle = i * phase
        release = min(1, (length - i) / (0.08 * RATE))
        if instrument == "keys":
            tone = math.sin(angle) + 0.22 * math.sin(2 * angle) + 0.12 * math.sin(3 * angle) * math.exp(-4 * t)
            env = min(1, t / 0.012) * math.exp(-3.9 * t)
        elif instrument == "bass":
            tone = math.sin(angle) + 0.15 * math.sin(2 * angle)
            env = min(1, t / 0.025) * math.exp(-2.0 * t)
        else:
            tone = math.sin(angle + 0.28 * math.sin(2 * angle))
            env = min(1, t / 0.18) * math.exp(-0.7 * t)
        sound.append(tone * env * release)
    return sound


def render_loop(path: Path, chords: tuple, bpm: int, shift: int, variation: int) -> None:
    beat = RATE * 60 / bpm
    total = round(BEATS * beat)
    mix = array("f", [0.0]) * total

    def add(at: float, note: int, timbre: str, length: float, gain: float) -> None:
        start = round(at * beat) % total
        samples = voice(note + shift, length, timbre)
        # Wrap the decay of the final notes into the first bar to avoid a silent seam.
        for index, sample in enumerate(samples):
            mix[(start + index) % total] += gain * sample

    for bar in range(8):
        chord = chords[bar % 4]
        downbeat = 4 * bar
        for n in (1, 2, 3):
            add(downbeat + n * 0.027, chord[n], "pad", 2.9, 0.055)
        for n in (0, 2.5):
            add(downbeat + n, chord[0] - 12, "bass", 1.2, 0.21)
        pattern = ((1, 3, 2, 1, 3), (2, 1, 3, 2, 3), (3, 2, 1, 3, 2))[variation]
        for index, (slot, degree) in enumerate(zip((0, 0.75, 1.5, 2.5, 3.25), pattern, strict=True)):
            note = chord[degree] + (12 if bar >= 4 and index == 3 else 0)
            add(downbeat + slot, note + 12, "keys", 1.0, 0.16 if index in (0, 3) else 0.11)
    pcm = array("h")
    delay = round(beat * 0.373)
    for i, dry in enumerate(mix):
        left = dry + mix[(i - delay) % total] * 0.19
        right = dry + mix[(i - 2 * delay) % total] * 0.17
        pcm.extend((round(32767 * left), round(32767 * right)))
    if sys.byteorder == "big":
        pcm.byteswap()
    with wave.open(str(path), "wb") as out:
        out.setparams((2, 2, RATE, 0, "NONE", ""))
        out.writeframes(pcm.tobytes())


def typeface(name: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(name, size)
    except OSError:
        return ImageFont.load_default(size=size)


def jacket(name: str) -> Image.Image:
    side = 720
    dusk = name == "afterglow"
    ink = "#352839" if dusk else "#133238"
    paper = "#ffda9a" if dusk else "#cae7c9"
    im = Image.new("RGB", (side, side), paper)
    d = ImageDraw.Draw(im)
    d.rectangle((23, 23, 696, 696), fill=ink)
    if dusk:
        # An amber disc drops into broad, overlapping ridgelines.
        d.ellipse((167, 75, 547, 455), fill="#ffba68")
        for row in range(13):
            y = 183 + 14 * row
            d.line((160, y, 552, y), fill=ink, width=1 + row // 5)
        for level, color in enumerate(("#a75756", "#8d4350", "#71384c", "#523245", "#352839")):
            contour = []
            for x in range(23, 698):
                y = 328 + 33 * level + 32 * math.sin(x * 0.010 + level * 0.63) + 11 * math.sin(x * 0.025 + level)
                contour.append((x, y))
            d.polygon([*contour, (697, 537), (23, 537)], fill=color)
            d.line(contour, fill="#e89c76", width=2)
    else:
        # A silkscreen of crossing currents inside an oversized glass lens.
        disc = Image.new("RGB", im.size, "#94c8b6")
        dd = ImageDraw.Draw(disc)
        for band in range(150):
            line = []
            for x in range(0, 721, 3):
                ridge = 24 * math.sin(x / 78 + band / 13) + 34 * math.cos(x / 147 - band / 16)
                line.append((x, band * 5 - 120 + ridge))
            dd.line(line, fill=("#245962", "#2e7074", "#5f9e94", "#abd2b8")[min(3, band // 36)], width=3)
        mask = Image.new("L", im.size)
        ImageDraw.Draw(mask).ellipse((133, 63, 589, 519), fill=255)
        im.paste(disc, mask=mask)
        for j in range(16):
            curve = [(x, 317 + (x - 360) * (-0.39) + 50 * math.sin(x / 113 + j / 19) + j * 2.2) for x in range(25, 696, 2)]
            d.line(curve, fill=("#a0ceba" if j % 3 == 0 else "#518886"), width=1)
    title = name.capitalize()
    serif = typeface("DejaVuSerif.ttf", 87)
    d.text((53, 522), title, font=serif, fill=paper, stroke_width=0)
    d.line((56, 637, 665, 637), fill=paper)
    d.text((56, 649), "EDOPAL   /   SYNTHETIC LOOPS", font=typeface("DejaVuSans.ttf", 15), fill=paper)
    # Deterministic paper grain softens the flat print colours at detail size.
    rng = random.Random(name)
    grain = Image.frombytes("L", im.size, rng.randbytes(side * side))
    return Image.blend(im, ImageOps.colorize(grain, ink, paper), 0.065)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    for name, tracks in ALBUMS.items():
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)
            jacket(name).save(source / "jacket.png")
            metadata = {}
            for i, (title, bpm, shift, roles) in enumerate(tracks):
                filename = f"{title}.wav"
                render_loop(source / filename, AFTERGLOW if name == "afterglow" else TIDEGLASS, bpm, shift, i)
                metadata[filename] = {"title": title, "suggested_roles": roles}
            write_json(source / "tracks.json", metadata)
            title = name.capitalize()
            build(
                source,
                args.root,
                slug=f"sample-{name}",
                name=title,
                game=f"{title} (sample)",
                platform="EDOPal",
                year=2026,
                cover=source / "jacket.png",
                credits=[
                    {
                        "what": "Original synthesized music and illustrated sample cover",
                        "source": "EDOPal",
                        "url": "https://github.com/cntrl-alt-lenny/edopal-packs/blob/main/music/make_samples.py",
                    }
                ],
            )


if __name__ == "__main__":
    main()
