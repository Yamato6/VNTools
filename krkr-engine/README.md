# KiriKiri Tools

Tools for translating games on the **KiriKiri 2** engine (KAG / KAGEX). They have been tested with **Noble Works** (ノーブル☆ワークス, Yuzusoft, 2010) with its 1.01 and 1.02 patches and the English fan patch. With them you can:
- open the `.xp3` archives and preview their images and layered sprites;
- extract everything inside them (images, sprites, music, voices, sound effects, video and scripts);
- extract and rebuild the text of the scene databases;
- put your edited files and translated text in a patch (`patch4.xp3`) that the game loads on top of its own files.

> **Author's notice.** Yuzusoft and other makers put a file in every `.xp3` saying that they do not want the archives extracted. The archives are not encrypted, but please respect that wish: use the extracted files only with your own copy of the game and do not share them. The tool skips that file and shows this notice.

## Features

- **Graphical interface** (CustomTkinter) and command line
- **Extract everything** in one step: the 45,000 files of the 13 archives of Noble Works (4 GB) in about one minute with 8 processes
  - TLG5 and TLG6 images are converted to PNG; PNG, music, voices, sound effects and video are saved unchanged
  - Layered sprites are also **composed**: each outfit and variant with the first face, as a full PNG
  - PSB files (motion data, E-mote) are also saved as readable JSON
  - Each file's checksum (adler32) is verified
  - Extraction runs in parallel, at low priority, and can be cancelled and resumed
- **Patches**: the engine loads `patch.xp3`, `patch2.xp3`, `patch3.xp3`… in order, and their files replace the game's. The tool writes your edited files to the next free patch, so the original archives are never touched
  - Only the files you edited since the extraction go in; edited TLG images are written back as TLG
- **Text**:
  - Extract the lines, character names, displayed names, choices and scene titles, with the speaker and the voice file of each line as comments
  - Optionally add the text of the game without its text patch as a reference on each line (for example the Japanese under the English patch)
  - Rebuild with your translation into the same patch. Lines can be any length, and KiriKiri uses Unicode, so no character table is needed

## Files

| File | Description |
|------|-------------|
| `krkr_xp3.py` | `.xp3` archives: reading, writing, load order and patch names |
| `krkr_tlg.py` | TLG5 and TLG6 images: decoding, and TLG5 writing |
| `krkr_image.py` | File type detection and layered sprites (layer tables, outfits, faces, composition) |
| `krkr_psb.py` | PSB (Packaged Struct Binary) to JSON |
| `krkr_script.py` | Scene databases (`scene.sdb`, `scenedata.sdb`, `scenepatch.sdb`): text extraction and rebuilding |
| `krkr_extract.py` | Full extraction and patch packing (shared by the CLI and the GUI) |
| `_krkr.c` | Native TLG5/TLG6 decoding, built into `_krkr.dll` |
| `krkr_common.py` | Shared helpers |
| `krkr_tool.py` | Command line tool (opens the GUI when run without arguments) |
| `krkr_gui.py` | Graphical interface |
| `build_exe.bat` | Builds `_krkr.dll` and a standalone `.exe` with PyInstaller |

## Requirements

- Python 3.11+ (the text tools open the databases in memory with `sqlite3`)
- Pillow, NumPy
- CustomTkinter (GUI only)
- gcc, optional, to build `_krkr.dll`

```bash
pip install -r requirements.txt
gcc -O2 -shared -static-libgcc -o _krkr.dll _krkr.c
```

Without `_krkr.dll` the tools use a pure Python version of the same code. It gives the same images but is much slower: a 325×1093 sprite takes 0.15 seconds instead of 0.005, and TLG6 images are slower still. `build_exe.bat` builds the DLL automatically when gcc is installed (for example from MSYS2) and puts it inside the `.exe`.

## Graphical Interface

```bash
python krkr_gui.py
```

- **XP3 Files**: open a `.xp3` and filter its files by name or type.
  - **Preview**: selecting a file shows its image over a checkerboard (to see transparency), with its size and format and the name of the PNG it is extracted as. Selecting a sprite's layer table (`fgimage`, e.g. `ひなたa_1.txt`) shows the composed sprite: ◀ ▶ (or the arrow keys) go through every outfit and face. Text, PSB and databases show their first lines or tables.
  - **Extract selected** / **Extract all**: save the selected files or the whole archive into `<output>/<archive>/`, with TLG images as PNG. **Extract game…** does the same for every archive of a game folder. **Compose sprites** also writes the composed sprites to `_composed`. With **Resume**, an interrupted extraction continues where it stopped; files are processed in parallel by the chosen number of workers.
  - **Pack**: makes a patch from a folder. From an extraction (one archive's folder or the whole output), only the files edited since then go in. Any other folder goes in whole.
- **Scripts**: choose the game folder, extract the text (with the optional reference), then rebuild it with your translation into a patch. A report lists every changed line.

The default output folders are next to the game folder, never inside it: the tools only read the game files. The patch is saved to `<game>_new\patchN.xp3`; copy it to the game folder yourself.

### Standalone executable

```bash
pip install -r requirements.txt
build_exe.bat
```

The result is `dist\KiriKiri_Tools_GUI.exe`, which runs without Python installed.

## Usage

### List files

```bash
python krkr_tool.py list <file.xp3>
```

### Extract

```bash
python krkr_tool.py extract <game_folder|file.xp3> <output_folder> [--resume] [--workers N] [--no-composed]
```

**Example:**
```bash
python krkr_tool.py extract "D:\Games\Noble Works" out --resume
```

Each archive goes to `<output>\<archive>\`, keeping the folders inside it.

| Contents | Output |
|----------|--------|
| TLG5 / TLG6 images | `NAME.png` |
| PSB (`.psb`) | `NAME.psb` and `NAME.psb.json` |
| Layered sprites (`fgimage`) | the layers as PNG, and `_composed\<pose>\<outfit>_diff_<n>_face_<face>.png` |
| PNG, Ogg, `.sli` loops, video, scripts (`.ks`, `.tjs`), tables, databases | Unchanged |

**Options:**
- `--resume`: skip the files already done. Finished names are listed in `<output>\<archive>\_progress.txt`
- `--workers`: number of parallel processes. The default is half of the CPU cores
- `--no-composed`: do not compose the layered sprites

Composed sprites use the smallest size of each pose (the game also has 2× and 2.8× versions for zooming); the preview shows every face. Extraction also writes `_files.json` in each archive folder. It holds a hash of every file, so that packing knows which ones you edited.

### Make a patch

```bash
python krkr_tool.py pack <folder> <patch.xp3>
```

**Example:**
```bash
python krkr_tool.py pack out "D:\Games\Noble Works_new\patch4.xp3"
```

`<folder>` is the extraction output, one archive's folder inside it, or any folder of files. Edited PNGs that were TLG images are written back as TLG5 (uncompressed, which the engine reads like any other); everything else goes in as is. If the patch already exists, its other files are kept, so images and text can go into the same patch.

A patch cannot have folders: every file goes in with its name only, and the engine finds it by that name. Two edited files with the same name in different folders are reported as an error.

Copy the patch to the game folder with the next free number: Noble Works with the English patch already has `patch3.xp3`, so yours is `patch4.xp3`. The numbers must be consecutive.

### Convert a PSB

```bash
python krkr_tool.py psb <file.psb> <output.json>
```

### Text

```bash
python krkr_tool.py text <game_folder> <text_folder> [--reference] [--json]
python krkr_tool.py text-rebuild <game_folder> <text_folder> <patch.xp3>
```

**Example:**
```bash
python krkr_tool.py text "D:\Games\Noble Works" text --reference
python krkr_tool.py text-rebuild "D:\Games\Noble Works" text "D:\Games\Noble Works_new\patch4.xp3"
```

`text` writes one file per scenario (`●001出会い.txt`…) and `_names.txt` with the character names. Each line is `KEY|TEXT`; translate only the text after `|`:

```
## scene 1 *0907
#> ９月７日
S1|September 7
# name: Takumi
#> 「……いい天気だなぁ」
T1.1|... Such nice weather.
# name: Takumi
# voice: xx24_001_001
T1.24|That bastard ran away!
# choice
#> バイトをＯＫする
C1.0|Take the job
```

| Key | Text |
|-----|------|
| `S<scene>` | Scene title (save screen, scene list) |
| `T<scene>.<n>` | A line of text |
| `N<scene>.<n>` | The name shown for that line, when it is not the character's usual name (`???`, `Girl`…) |
| `C<scene>.<n>` | A choice |
| `M<id>` (`_names.txt`) | A character name, as shown for every line of that character |

Lines starting with `#` are comments: `# name:` is the speaker, `# voice:` the voice file and `#>` the reference. `\n` is a line break; `%30…%0` and other `%` codes are text size and style codes of the engine, keep them.

- `--reference`: adds the text of the game **without** the archive that has its text patch (`scenepatch.sdb`). In Noble Works with the English patch, the text is English and the reference is the Japanese
- `--json`: also writes `json\<scenario>.json` with the speaker, voice and reference of each line

`text-rebuild` compares the files with the game's text and writes new `scene.sdb` (names and titles) and `scenepatch.sdb` (lines and choices) to the patch. If nothing changed, no file is written.

## Supported game

Noble Works (Yuzusoft, 2010), Japanese version 1.02 with the English fan patch (`patch3.xp3`):

| Archive | Contents | Files |
|---------|----------|-------|
| `bgimage.xp3` | Backgrounds | 90 PNG |
| `evimage.xp3` | Event CGs | 1,796 PNG |
| `fgimage.xp3` | Sprites: 24 poses × 3 sizes | 1,899 TLG5 layers, 72 layer tables, 24 outfit and face lists, 3 tables |
| `bgm.xp3` | Music | 33 Ogg, 31 loops |
| `sound.xp3` | Sound effects | 311 Ogg, 81 loops |
| `voice.xp3` | Voices | 26,586 Ogg, 13,342 `.sli` files |
| `video.xp3` | Opening and animations | 13 WMV, 21 PSB |
| `scenario.xp3` | Scenarios and scene databases | 3 `.sdb`, 28 scripts |
| `data.xp3` | Interface, system scripts and fonts | 486 PNG, 1 TLG, 8 prerendered fonts, scripts |
| `main.xp3` | Startup and settings | 49 |
| `patch.xp3`, `patch2.xp3` | Official patches 1.01 and 1.02 | 97, 7 |
| `patch3.xp3` | English patch: interface images and the English text (`scenepatch.sdb`) | 82 |

The text has 57,690 lines in 443 scenes, 107 choices and 78 character names.

## Format Reference

### XP3

```
char "XP3\r\n \n\x1a\x8bg\x01" · i64 index_offset
(2.30: at that offset u32 0x80 · u8 · i64 real index_offset)
index: u8 type (1 = zlib) · i64 packed_size · i64 size · data
```

The index is a list of chunks `char sig[4] · i64 size · body`. Each `File` chunk contains `info` (u32 flags · i64 size · i64 stored size · u16 length · UTF-16 name), `segm` (one or more `u32 zlib · i64 offset · i64 size · i64 stored size`) and `adlr` (adler32 of the file). The archives of Noble Works are not encrypted: flag `0x80000000` only marks them as protected.

The engine (`system/Initialize.tjs`) mounts `patch.xp3` and then `patch2.xp3`, `patch3.xp3`… while they exist. Files in a later archive replace those with the same name, and the files of a patch are found by name only (no folders).

### TLG

```
TLG5: "TLG5.0\0raw\x1a" · u8 colors · u32 width · u32 height · u32 block_height
      · u32 block_sizes[] · per block and color: u8 mark · u32 size · data
TLG6: "TLG6.0\0raw\x1a" · u8 colors · 3 bytes · u32 width · u32 height · u32 max_bits
      · filter types (LZSS) · per 8-line group and color: Golomb-coded values
```

- **TLG5**: each color plane is LZSS-compressed (4 KB window shared by the whole image) when `mark` is 0, or stored raw. Values are differences from the left pixel and the pixel above, with blue and red relative to green.
- **TLG6**: values are Golomb-coded, predicted per 8×8 block with MED or average and a color correlation filter (32 types). The game does not use TLG6, so that decoder is tested only against its own Python version.
- A `TLG0.0\0sds\x1a` wrapper adds a size and tags around either.

### Layered sprites

`<pose>_<size>.txt` is a tab-separated table exported from Photoshop: the canvas size, then each layer (top to bottom) with its position, size, blend type (13 normal, 16 multiply), opacity and `layer_id`. `<pose>_<size>_<layer_id>.tlg` is the image of each layer. `<pose>_info.txt` says which layers form each outfit (`dress <name> diff <n> <layer>`) and each face (`face <n> base <layer>`).

### Scene databases

The text is not in the `.ks` scripts but in SQLite databases, read by `system/KAGEnvSceneDB.tjs`:

| Database | Tables |
|----------|--------|
| `scene.sdb` | `name(id, name)`, `storage(id, name)`, `scene(id, storage, label, title, ...)` |
| `scenedata.sdb` | `text(scene, idx, name, disp, text, voice, flag, state)`, `next(scene, idx, next, eval, exp, type, text, ...)`, `eval` |
| `scenepatch.sdb` | Same tables as `scenedata.sdb`: for every scene it contains, the whole scene is read from it |

`state` is a PSB wrapped in `mdf` (zlib) with the state of the scene at that line (music, background, sprites). The English patch fills the `voice` column with the voice file of each line.

### PSB

```
"PSB\0" · u16 version · u16 encrypted · u32 header_size · u32 names · u32 strings
· u32 string_data · u32 chunk_offsets · u32 chunk_sizes · u32 chunk_data · u32 root
```

Values carry their type in the first byte: null, booleans, integers of 0–8 bytes, floats, integer arrays, strings (by index), resources, lists and objects. Key names are stored in a trie. Encrypted PSB files (E-mote) are not supported.

## Credits

- [GARbro](https://github.com/morkt/GARbro) (MIT): reference for the XP3 and TLG formats; the TLG5/TLG6 decoders follow its port of the KiriKiri 2 code (W.Dee and contributors)
- [FreeMote](https://github.com/UlyssesWu/FreeMote): reference for the PSB format
- The sprite tables, the scene databases and the patch order were checked against the game's own files and scripts
