# NeXAS Tools

Tools for translating **Maji de Watashi ni Koi Shinasai! S** (Minato Soft, 2012), which runs on the **NeXAS** engine by Giga: extract everything in the game's `.pac` archives (images, audio, video, scripts and data tables), extract and rebuild the script text, and repack edited files, either into a full `.pac` or into a small patch archive that the game loads on top of the original files.

## Features

- **Graphical interface** (CustomTkinter) and command line
- **Extract everything** in one step: 51,000 files from the 13 archives (about 3.5 GB), by category (images, audio, video, scripts, data). Interrupted extractions resume where they stopped
- **Scripts**: extract the text of the 264 scripts (messages, character names, choices and system messages) and rebuild them. **Translated lines can be any length**: the scripts refer to their strings by number, so nothing else has to be relocated
- **Reference text**: extract the Japanese scripts with the English patch's text next to each line (or the other way round)
- **Data tables** (`.dat`): extract and rebuild the interface texts, music titles and event names, exported as CSV for viewing
- **Repack** edited files: only the files you changed are re-encoded, and everything else is copied unchanged
- **Patch archives**: write only the changed files to `Update4.pac`…`Update8.pac`. The game loads them on top of its own archives, so the original files are never modified
- Converts FIL transition masks to PNG and back, decodes NMV effect animations (version 4) to PNG frames, and composes the character sprites (SPM) into full images
- Supports a character table (`--charmap`, same format as FVP Tools) for letters that Shift-JIS lacks, such as `ñ` or `á`

## Files

| File | Description |
|------|-------------|
| `nexas_pac.py` | `.pac` archives: reading, writing and repacking |
| `nexas_compress.py` | Huffman (archive index) and LZSS compression |
| `nexas_script.py` | Script (`.bin`) and table (`.dat`) text: extraction, rebuilding and listing |
| `nexas_formats.py` | FIL masks, NMV animations, SPM sprites and DAT tables |
| `nexas_extract.py` | Full extraction and repacking (shared by the CLI and the GUI) |
| `nexas_tool.py` | Command line tool (opens the GUI when run without arguments) |
| `nexas_gui.py` | Graphical interface |
| `build_exe.bat` | Builds a standalone `.exe` with PyInstaller |

## Requirements

- Python 3.8+
- Pillow
- CustomTkinter (GUI only)

```bash
pip install -r requirements.txt
```

## Graphical Interface

```bash
python nexas_gui.py
```

- **Extract**: pick the game folder (or a single `.pac`) and an output folder, choose the categories and extract, with a live preview of the images.
- **Repack**: pick the original `.pac`, the extracted folder and the output `.pac`. By default only the changed files are written, as a patch named after the first free `UpdateN.pac`.
- **Text**: extract the text of `Script.pac` (or `Update3.pac`, `Config.pac`, `Update2.pac`) with an optional reference archive, then rebuild it with your translation, with a report of the lines that could not be encoded.
- **PAC Files**: open a `.pac`, filter its files by name or type and extract all of them or only the selected ones. "Pack folder…" packs a folder into a `.pac`.

The default output folders are next to the game folder, never inside it: the tools only read the game files.

### Standalone executable

```bash
pip install -r requirements.txt
build_exe.bat
```

The result is `dist\NeXAS_Tools_GUI.exe`, which runs without Python installed.

## Usage

### List files

```bash
python nexas_tool.py list <file.pac>
```

### Extract

```bash
python nexas_tool.py extract <game_folder|file.pac> <output_folder> [--only images,audio,video,scripts,data]
                             [--resume] [--raw] [--no-extras] [--sprites]
```

**Example:**
```bash
python nexas_tool.py extract "D:\Games\Majikoi S" out --resume
```

Each archive goes to `<output>\<archive>\`, with every file under its own name. With the `video` category, the movies in the game's `Movie` folder are copied to `<output>\Movie\`.

| Files | Output |
|-------|--------|
| `.png`, `.bmp`, `.jpg`, `.ogg`, `.mpg`, `.bin`, `.dat`, `.spm`, `.nmv` | Unchanged |
| `.fil` | `NAME.fil.png` (8-bit grayscale), unless `--raw` is given |

Extras, for viewing only (repacking ignores them):

| Folder | Contents |
|--------|----------|
| `_frames\NAME\` | NMV animation frames as PNG with transparency, and `_timing.json` (version 4 only; see below) |
| `_spm\` | SPM sprite layouts as JSON |
| `_csv\` | `.dat` tables as CSV |
| `_sprites\` | Composed character sprites, one PNG per pattern (only with `--sprites`; takes a while for `Stand.pac`) |

**Options:**
- `--only`: only these categories. Images: `png`, `bmp`, `jpg`, `fil`, `spm`; audio: `ogg`; video: `nmv`, `mpg`; scripts: `bin`; data: `dat`
- `--resume`: skip the files already done. Finished names are listed in `<output>\<archive>\_progress.txt`
- `--raw`: keep the FIL masks as `.fil`
- `--no-extras`: do not write the extras
- `--sprites`: compose the sprites

Extraction also writes `_files.json` in each archive folder, with a hash of every file, so that repacking knows which ones you edited.

### Repack

```bash
python nexas_tool.py repack <original.pac> <folder> <output.pac> [--patch] [--add-new] [--allow-resize]
```

`folder` is the output of `extract` (or the folder of that archive inside it). Only the files whose content changed since the extraction are taken; `NAME.fil.png` is converted back to FIL. Images must keep their original size unless `--allow-resize` is given. If any file cannot be encoded, no file is written and the errors are listed.

- `--patch`: the output only holds the changed files. Save it in the game folder as `Update4.pac` (or the next free number up to `Update8.pac`; the English patch already uses `Update2.pac` and `Update3.pac`)
- `--add-new`: also add the files of the folder that are not in the original archive

**Example:**
```bash
python nexas_tool.py extract "D:\Games\Majikoi S\Update2.pac" out
# edit out\Update2\*.png…
python nexas_tool.py repack "D:\Games\Majikoi S\Update2.pac" out Update4.pac --patch
```

### Pack a folder

```bash
python nexas_tool.py pack <folder> <output.pac> [--like <original.pac>] [--compression N]
```

Packs every file of a folder (and its subfolders). Files starting with `_` are skipped. With `--like`, the files keep the order and compression of an original archive; otherwise they are sorted by name and stored with the given compression (`0` none, the default, or `3` zlib).

### Text

```bash
python nexas_tool.py text <file.pac> <text_folder> [--reference <other.pac>] [--json] [--listing] [--charmap <map.txt> --reverse]
python nexas_tool.py text-rebuild <file.pac> <text_folder> <output.pac> [--patch] [--charmap <map.txt>]
```

`text` writes one file per script, `<script>.bin.txt`, with one line per text: the string number and the text. Character names, choices and system messages are marked with a comment; with `--reference`, the reference text goes before each line as a `#>` comment:

```
#> One day, the attraction named the 「Land of Battle」@nwas busy with tourists.
0006|名所「決闘の地」は観光客でごった返していた。
#> Yamato
# name
0008|大和
#> @vS050_A1_0001「Living on, tough and strong♪」
0012|@vS050_A1_0001「ねばーり強く、いきてゆくーんだ♪」
#> 燕@Girl
# name
0013|燕@少女
```

Translate only the text after `|`. Keep the control codes:

- `@v<voice>` at the start of a line plays a voice file (`@vS050_A1_0001` is `s050_a1_0001.ogg` in `Voice.pac`)
- `@n` is a line break
- Character names written as `key@display` (`燕@少女`): translate only the part after `@`. The part before it identifies the character

`--json` also writes `json\<script>.bin.json` with the type, text, reference and voice file of each line, and `--listing` writes a disassembly of every script to `listing\`.

On `.dat` tables (`Config.pac`, `Update2.pac`), each line is a cell, `ROW:COLUMN|TEXT`. Cells that look like file names or identifiers are left out. `system.dat` holds the game's registry key and window titles: translate only the messages.

`text-rebuild` takes the original archive and the folder with the translated files. Archives and scripts without a text file, or without changes, are copied unchanged. With `--patch`, the output only holds the changed scripts.

**Which archive to translate:** the game reads `Update3.pac` (English patch) before `Script.pac`. Translate from `Script.pac` with `--reference Update3.pac` (or from `Update3.pac` with `--reference Script.pac`), rebuild with `--patch` and save the result as `Update4.pac` (or the next free number): it overrides both.

Only **Shift-JIS (CP932)** text is supported. Characters outside it (ñ, á, ¿…) keep the original line and are listed in the report, unless a character table maps them (`--charmap`, one `á=ｱ` pair per line, same format as FVP Tools) to characters that a custom font draws as them. Saved games may not work after the scripts change.

**Example:**
```bash
python nexas_tool.py text "D:\Games\Majikoi S\Script.pac" text --reference "D:\Games\Majikoi S\Update3.pac"
# translate text\*.txt…
python nexas_tool.py text-rebuild "D:\Games\Majikoi S\Script.pac" text Update4.pac --patch
```

## Game Files

| File | Contents | Entries |
|------|----------|---------|
| `Script.pac` | Scripts (Japanese) | 264 `.bin` |
| `Update3.pac` | Scripts (English patch) | 264 `.bin` |
| `Config.pac` | Data tables | 26 `.dat` |
| `Update2.pac` | English interface | 340 PNG, 5 `.dat`, 1 JPG, 1 BMP |
| `System.pac` | Interface | 494 PNG, 46 FIL, 9 SPM, 2 BMP |
| `Visual.pac` | Event CG `ev_*` and backgrounds `bg_*` | 1,885 PNG |
| `Stand.pac` | Character sprites | 5,702 PNG, 1,303 SPM |
| `Face.pac` | Faces for the message window | 3,095 PNG |
| `Thumbnail.pac` | Thumbnails | 1,221 BMP |
| `Effect.pac` | Effects | 248 NMV, 24 MPG |
| `Bgm.pac`, `Se.pac`, `Voice.pac` | Music, sound effects and voices | 56, 495 and 33,904 OGG |
| `Movie\` (folder) | Opening and endings | 13 MPG |

The game's own configuration (`system.dat`) lists the archives it loads, in this order: `Update8`…`Update2`, `Update`, then the base archives. A file in a patch archive replaces the file with the same name in any archive below it.

## File Formats

All values are little-endian.

### PAC archive

```
char  magic[4] = "PAC\0"
u32   entry_count
u32   compression       0 none, 1 LZSS, 2 Huffman, 3 zlib, 4 zlib only when smaller
...   entry data (from offset 12)
...   index: k bytes, every byte inverted (~b), Huffman-compressed
u32   k
```

The decompressed index has `entry_count` entries of 0x4C bytes: `char name[0x40]` (Shift-JIS), `u32 offset`, `u32 size`, `u32 stored_size`. Huffman data is read MSB first: the tree comes first in preorder (bit 1 = internal node, then its left and right children; bit 0 = leaf followed by an 8-bit symbol), then the codes (0 = left, 1 = right). Older NeXAS games store the index uncompressed at offset 12, with 0x40- or 0x20-byte names; the tool reads those too (not tested on a real game).

When repacking, unchanged entries are copied byte for byte, but the index is compressed with a new Huffman tree, so the archive is not byte-identical to the original even when nothing changed.

### Script (`.bin`)

```
u32 n1;  n1 × { u32 opcode; u32 arg }     code 1 (initialization)
u32 n2;  n2 × { u32 opcode; u32 arg }     code 2 (the script)
u32 ns;  ns × Shift-JIS string + \0       string table
...      symbol table and other tables (copied unchanged)
```

The code is a stack machine that refers to strings by number. `push k` (opcode 0) followed by `arg 1` (opcode 5) passes string `k` to the next `call` (opcode 7, `arg = argc << 16 | function`). The type of each string comes from the function that receives it:

| Function | Argument | Text |
|----------|----------|------|
| `0x1AA`, `0x5D`, `0x5E`, `0x1AB` | 0, 3, 0, 0 | Message |
| `0x1A9` | 0 | Character name |
| `0x56` | 1 | Choice |
| `0x6A`, `0x1D`, `0x62`, `0x0F`, `0x11` | 0 or 1 | System message |

A few strings (for example the protagonist's name in some scripts) are not passed directly to these functions; they are included because the same text is a name or message elsewhere. Strings that are not text (file names, effect names, `@v` markers) are never extracted. This table was worked out from the 264 scripts and checked against the English patch: rebuilding `Script.pac` with the patch's text gives the patch's scripts byte for byte, except for 89 scripts where the patch wrote a wrong byte in the symbol table, and 7 strings outside the extracted text that the patch also changed (6 debug messages and one string in `y5_0000_0.bin`).

### DAT table

```
u32 column_count
column_count × u32 type     1 = Shift-JIS string + \0, 2 = i32
rows until the end of the file
```

### FIL mask

`u32 width, u32 height`, then one byte of gray per pixel. Used for screen transitions (`rule*.fil`) and for a few custom glyphs (`gaiji*.fil`).

### NMV animation

```
u16  version (0, 3 or 4)
char "GraF"
u32  width, height
u8   bytes per pixel (3 or 4)
frames: the first one with no header, the others after "GraK" + u32 time in ms
    u32 size, then
    version 4: u32 alpha_size, grayscale JPEG (alpha), color JPEG
    versions 0 and 3: zlib data with a second, unknown codec
```

Only version 4 (41 of the 248 animations) is converted to PNG frames; versions 0 and 3 are extracted as `.nmv`.

### SPM sprite

```
char "SPM VER-2.00\0"
u32  pattern_count
per pattern:
    u32 part_count, i32 width, height, box (left, top, right, bottom), 4 × u32
    part_count × 14 i32: image, destination (left, top, right, bottom), width, height,
                         source (left, top, right, bottom), 3 flags
u32  image_count, image names (Shift-JIS + \0)
...  unknown data
```

Each pattern is one look of the character (expressions, mouth states…): the body (`_000`) plus face parts (`_010`, `_020`…) placed at their destinations. Some patterns use images that the game does not include; they are skipped when composing.

## License

Released under the [MIT License](../LICENSE). No game data is included; you need your own copy of the game. Please respect the original game developers and publishers.

## Credits

- [GARbro](https://github.com/morkt/GARbro) (MIT): reference for the PAC archive and its Huffman compression
- The script, table, SPM and NMV formats were worked out from the game's own files
