# Music packs

The catalogue spans Yu-Gi-Oh! games with background music across handheld, console, PC, arcade, browser and mobile releases. Game years are the first release in any region. Each game manifest credits its cover, links to the original image, and records its native dimensions and region. A 1,200 px WebP accompanies each entry. The 480 px thumbnails live in `thumbs/`. Game entries have `audio: "pending"` and an empty `tracks` list.

`edopro-default` lists the seven factory tracks from EDOPro 41.0.2 by scene under `builtin_tracks`. The `builtin` flag identifies this entry for restoration. Its paths are relative to the game installation.

## Build from local audio

Use the EDOPal Python environment with Pillow and miniaudio. Start with WAV or FLAC masters when available. Ogg Vorbis and MP3 inputs also work. Put the songs in one folder and choose a cover image. From the application checkout, run the following with your own paths and attribution.

```sh
.venv/bin/python packs/music/build_pack.py ./masters \
  --slug evening-studies --name "Evening Studies" \
  --game "Evening Studies" --year 2026 --platform "EDOPal" \
  --cover ./jacket.png --credit "Music and artwork by Ada Lane" \
  --version 1.0.0 --root ./pack-staging
```

`--credit-url` accepts a source or artist link. The default output root is the packs checkout. A fresh staging directory lets you compare an updated version with the existing files.

The builder writes `music/<slug>/manifest.json`, `cover.webp` and `audio/`, plus gallery files under `thumbs/` and `previews/`. It measures duration from the final encoded files and records a SHA-256 digest for every file inside the pack except the manifest itself.

With FFmpeg on `PATH`, the builder measures loudness, applies a second pass targeting −16 LUFS integrated and −1 dBTP, and encodes stereo Ogg Vorbis at 44.1 kHz and a nominal 160 kb/s. The gallery clip uses the first 20 seconds of the first track at a nominal 96 kb/s. FFmpeg must include `loudnorm` and `libvorbis`. Without FFmpeg, the builder copies each source track byte for byte and streams a mono PCM WAV preview of up to 20 seconds. Audition the preview, the loop boundary and the full track before packaging.

## Set titles, order and suggested roles

By default, tracks follow filename order and use the filename stem for their title. An optional `tracks.json` alongside the masters specifies their display order and role suggestions. The first track supplies the gallery preview. Unlisted tracks follow in filename order.

```json
{
  "01-first-light.wav": {"title": "First Light", "suggested_roles": ["menu", "deck"]},
  "02-night-flight.flac": {"title": "Night Flight", "suggested_roles": ["duel", "win"]}
}
```

The five role IDs are `menu`, `deck`, `duel`, `win` and `lose`. Listeners can assign any track to any role.

## Rebuild the catalog

```sh
.venv/bin/python packs/tools/build_catalog.py --root ./pack-staging
```

The shared catalog builder validates each manifest, writes a ZIP under `dist/`, and creates the top-level `catalog.json`. Each row includes ZIP size and SHA-256, gallery URLs, and track metadata. Its local rows point to pack directories for `LocalPacks`. The builder also supports `--release-base` and `--media-base` for release URLs. The application repository documents the format in `docs/packs.md`.
