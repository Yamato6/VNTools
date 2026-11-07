# BGI Tools

Tools for translating games on the **BGI / Ethornell** engine (Buriko General Interpreter). They have been tested with **Daitoshokan no Hitsujikai** (大図書館の羊飼い, August, 2013) and **Koi to Senkyo to Chocolate HR** (恋と選挙とチョコレート HR, sprite, 2020), whose entries are encrypted with BSE. With them you can:
- open the `.arc` archives and preview their images;
- extract everything inside them (images, music, voices, sound effects, video and scripts);
- extract and rebuild the script text;
- pack edited files back into archives that the game reads.

## Features

- **Graphical interface** (CustomTkinter) and command line
- **Extract everything** in one step: about 41,000 files from the 64 archives of Daitoshokan no Hitsujikai (7 GB), in about 3 minutes with 8 processes
  - CompressedBG images (v1 and v2, with transparency) and BGI images are converted to PNG
  - Music, voices and sound effects are saved as `.ogg`; video, fonts and system scripts unchanged
  - Loose files in the game folder, such as those of a fan patch, are extracted to a separate folder
  - Extraction runs in parallel, at low priority, and can be cancelled and resumed
- **Repack** the files you edited:
  - Only the changed files are re-encoded: edited PNGs become BGI images and edited `.ogg` files become `bw` audio
  - Everything else is copied byte for byte
- **Scripts**:
  - Extract the text, character names and choices of every script, with the voice file of each line as a comment
  - Optionally add the text of the same scripts in another language (for example the English patch) as a reference on each line
  - Rebuild the scripts with your translation. **Translated lines can be any length**: new strings are added at the end of the script and only the references to them change
- Supports a character table (`--charmap`, same format as the other VNTools) for letters that Shift-JIS lacks, such as `ñ` or `á`

## Files

| File | Description |
|------|-------------|
| `bgi_arc.py` | `.arc` archives (`PackFile` and `BURIKO ARC20`): reading and writing |
| `bgi_codecs.py` | Key generator, DSC compression and BSE encryption |
| `bgi_image.py` | CompressedBG (v1 and v2) and BGI images; file type detection |
| `bgi_audio.py` | `bw` audio (Ogg Vorbis with a header) |
| `bgi_script.py` | Compiled scripts (`BurikoCompiledScriptVer1.00`): text extraction and rebuilding |
| `bgi_extract.py` | Full extraction, repacking and packing (shared by the CLI and the GUI) |
| `_bgi.c` | Native CompressedBG decoding, built into `_bgi.dll` |
| `bgi_common.py` | Shared helpers and the character table |
| `bgi_tool.py` | Command line tool (opens the GUI when run without arguments) |
| `bgi_gui.py` | Graphical interface |
| `build_exe.bat` | Builds `_bgi.dll` and a standalone `.exe` with PyInstaller |

## Requirements

- Python 3.8+
- Pillow, NumPy
- CustomTkinter (GUI only)
- gcc, optional, to build `_bgi.dll`

```bash
pip install -r requirements.txt
gcc -O2 -shared -static-libgcc -o _bgi.dll _bgi.c
```

Without `_bgi.dll` the tools use a pure Python version of the same code. It gives the same images but is about 100 times slower: a 1280×720 background takes 4 seconds instead of 0.05. `build_exe.bat` builds the DLL automatically when gcc is installed (for example from MSYS2) and puts it inside the `.exe`.

## Graphical Interface

```bash
python bgi_gui.py
```

- **ARC Files**: open a `.arc` and filter its files by name or type.
  - **Preview**: selecting a file shows its image over a checkerboard (to see transparency), with its size and format and the name of the PNG it is extracted as. Audio shows its length, and scripts their first lines.
  - **Extract selected** / **Extract all**: save the selected files or the whole archive into `<output>/<archive>/`, with the images as PNG, the audio as `.ogg` and the rest as is. **Extract game…** does the same for every archive of a game folder, and saves its loose files to `<output>/loose_files/`. With **Resume**, an interrupted extraction continues where it stopped; files are processed in parallel by the chosen number of workers.
  - **Pack**: a folder from the extraction is repacked over the open `.arc`, and only the edited files change (images must keep their size). Any other folder becomes a new `.arc` with the order of the open one.
- **Scripts**: extract the text of the scripts archive (`data01000.arc`) with an optional reference (a folder or `.arc` with the same scripts in another language), then rebuild it with your translation. A report lists the lines that could not be encoded.

The default output folders are next to the game folder, never inside it: the tools only read the game files.

### Standalone executable

```bash
pip install -r requirements.txt
build_exe.bat
```

The result is `dist\BGI_Tools_GUI.exe`, which runs without Python installed.

## Usage

### List files

```bash
python bgi_tool.py list <file.arc>
```

### Extract

```bash
python bgi_tool.py extract <game_folder|file.arc> <output_folder> [--resume] [--workers N] [--no-loose]
```

**Example:**
```bash
python bgi_tool.py extract "D:\Games\Daitoshokan no Hitsujikai" out --resume
```

Each archive goes to `<output>\<archive>\`. The files of the archives have no extension; the tool finds their type from their contents.

| Contents | Output |
|----------|--------|
| CompressedBG and BGI images | `NAME.png` |
| `bw` audio | `NAME.ogg` |
| Scripts, system scripts (`._bp`), DSC data | `NAME`, without the DSC compression |
| Fonts, video (`.otf`, `.bmv`, `.mpg`), text | Unchanged |

With a game folder, the loose files the engine uses (images, scripts and audio next to the `.arc` files, such as those of a fan patch) go to `<output>\loose_files\`.

**Options:**
- `--resume`: skip the files already done. Finished names are listed in `<output>\<archive>\_progress.txt`
- `--workers`: number of parallel processes. The default is half of the CPU cores
- `--no-loose`: do not extract the loose files of the game folder

Extraction also writes `_files.json` in each archive folder. It holds a hash of every file, so that repacking knows which ones you edited.

### Repack

```bash
python bgi_tool.py repack <original.arc> <folder> <output.arc> [--add-new]
```

`<folder>` is the extraction output or the archive's folder inside it. Edited images are stored as uncompressed BGI images (inside a DSC block, like the system images of `sysgrp.arc`) and must keep their size; edited `.ogg` files get their `bw` header back. Everything else is copied unchanged. With `--add-new`, files that are not in the original are added too (PNG as images and `.ogg` as audio, named without the extension).

Replace the game's archive with the new one (keep a copy of the original).

### Pack a folder

```bash
python bgi_tool.py pack <folder> <output.arc> [--like <original.arc>]
```

Packs every file of a folder: `.png` files as images, `.ogg` files as audio (both named without the extension) and the rest unchanged. With `--like`, the files follow the order of an original archive.

### Join music loops

```bash
python bgi_tool.py join-loops <music_folder> <output_folder>
```

The music is split in two parts: `NAME_head`, played once, and `NAME_loop`, which repeats. This joins each pair into `NAME.ogg` for listening.

### Text

```bash
python bgi_tool.py text <scripts.arc> <text_folder> [--reference <folder|file.arc>] [--json]
                        [--charmap <map.txt> --reverse]
python bgi_tool.py text-rebuild <scripts.arc> <text_folder> <output.arc> [--charmap <map.txt>]
```

**Example:**
```bash
python bgi_tool.py text "D:\Games\Daitoshokan no Hitsujikai\data01000.arc" text --reference "D:\Games\Daitoshokan no Hitsujikai"
```

One file per script, `<script>.txt`:

```
#> 8:10 AM.
0002|午前８時１０分。
#> Kakei
# name
0012|筧
#> "Oh... You shouldn't stop so suddenly like that. It's dangerous."
0013|「おっと……突っ立ってると危ないですよ」
# voice: tos120100010
#> Passerby
# name
0015|通行人
```

- Translate only the text after `|`. Lines that start with `#` are comments: `# name` and `# other` mark character names and other strings (choices, lines spoken together), `# voice:` gives the voice file, and `#>` the reference text.
- `--reference`: a folder with the same scripts as loose files (for example the game folder with the English patch) or a `.arc`. The reference must have the same strings in the same order; scripts where it does not are extracted without it.
- `--json`: also write `<text_folder>\json\<script>.json` with every line, its type, voice and reference.
- `--charmap`: character table. `--reverse` applies it backwards, to extract an already translated script.

`text-rebuild` writes a new archive in which only the changed scripts differ. A script line can be any length.

### Character table

Same format as the other VNTools:

```
# comment
á=ｱ
é=ｲ
ñ=ﾅ
```

When rebuilding, every `á` is written as `ｱ`, which a custom font can draw as `á`. Characters that are not in Shift-JIS and not in the table are reported, and the original line is kept.

## Game Files

Daitoshokan no Hitsujikai:

| File | Contents | Entries |
|------|----------|---------|
| `data01000.arc` | Scripts | 232 |
| `data02001.arc` … `data02111.arc` | Character faces (`fac_*`) and sprites (`tat_*`) | 6,234 CompressedBG v2 |
| `data02400.arc`, `data02410.arc` | Backgrounds | 207 CompressedBG v1 |
| `data02500.arc`, `data02510.arc` | Event CG | 420 CompressedBG v1 |
| `data02800.arc` … `data02850.arc` | Effects, cut-ins, title cards, buttons, logos | 2,160 CompressedBG (v1 and v2) |
| `data02890.arc` | Opening and effect videos | 24 `.bmv`, 2 `.mpg` |
| `data02900.arc` | Transition masks | 58 CompressedBG v1 (8-bit) |
| `data03000.arc` | Sound effects | 568 `bw` |
| `data04001.arc` … `data04051.arc` | Voices | 29,790 `bw` |
| `data05000.arc`, `data05010.arc` | Music and ambient sound (`_head` / `_loop` pairs) | 347 `bw` |
| `data10001.arc` … `data10005.arc` | Interface themes | 17 each |
| `system.arc`, `sysprg.arc` | System scripts (`._bp`), system sounds, fonts | 28, 95 |
| `sysgrp.arc` | Interface graphics (BGI images in DSC) | 647 |

The English patch adds loose files to the game folder (scripts `tos*`, images, `._bp` system scripts and a font); the engine uses them instead of the files in the archives.

## File Formats

All values are little-endian.

### ARC archive

```
char  magic[12] = "PackFile    "
u32   entry_count
entry_count × { char name[0x10]; u32 offset; u32 size; u8 reserved[8] }
...   data (offsets start after the index)
```

`BURIKO ARC20` archives use 0x80-byte entries with a 0x60-byte name.

### Key generator

DSC and CompressedBG encrypt part of their data with this generator:

```
v0  = 20021 * (key & 0xFFFF)
v1  = (magic | key >> 16) * 20021 + key * 346
v1  = (v1 + (v0 >> 16)) & 0xFFFF
key = (v1 << 16) + (v0 & 0xFFFF) + 1;   returns v1 & 0xFF
```

### DSC

```
char "DSC FORMAT 1.00\0" · u32 key · u32 size · u32 code_count · u32 0
u8   depths[512]      each byte minus the next key byte (magic = "DS" << 16)
...  bit stream (most significant bit first)
```

The depths build a canonical Huffman tree of 512 codes. Codes below 256 are literal bytes; the others copy `(code & 0xFF) + 2` bytes from `12 bits + 2` bytes back. The tool writes edited files as a DSC with only literals at depth 8, which is valid but not compressed.

### BSE

Later games (Koi to Senkyo to Chocolate HR) encrypt each entry with BSE. Only the 0x40 bytes after the header are encrypted; the rest of the entry (usually a CompressedBG) follows unchanged:

```
char "BSE 1.0" or "BSE 1.1" · u8 0 · u16 version (0x100 / 0x101) · u16 checksum · u32 key
u8   encrypted[0x40] · ...rest of the data
```

For each of the 64 bytes the generator gives the position (`& 0x3F`, skipping positions already done), a rotation (`& 7`), its direction (`& 1`, 0 = right) and a value to subtract. The generator works with signed 32-bit integers:

```
1.0: v = ((key * 257 >> 8) + key * 97 + 23) ^ 0xA6CD9B75
1.1: v = ((key * 127 >> 7) + key * 83 + 53) ^ 0xB97A7E5C
key = rotate_right(v, 16)
```

Edited files are written without BSE.

### CompressedBG

```
char "CompressedBG___" · u16 width · u16 height · i32 bpp · i32 data_size · u32 key
i32 encoded_size · u8 checksum · u8 xor · u16 version
u8  encoded[encoded_size]      each byte minus the next key byte (magic 0)
```

- **Version 1**: `encoded` holds 256 weights for a Huffman tree. The decoded bytes alternate runs of data and runs of zeros (variable-length sizes). Each byte is then added to the average of its left and upper neighbours.
- **Version 2**: `encoded` holds two quantization tables. Then come 16 and 176 weights for two Huffman trees, a table of row offsets and, for each row of 8×8 blocks, the DC and AC coefficients. Blocks go through an inverse DCT and YCbCr→RGB. 32-bit images end with an alpha channel compressed with an LZ that uses 2D distances.

### BGI image

```
i16 width · i16 height · i16 bpp (8, 24 or 32) · i16 flag (0) · u8 zero[8]
pixels: BGR or BGRA, top to bottom
```

### bw audio

```
u32 header_size (0x40) · char "bw  " · u32 ogg_size · u32 samples · u32 rate · u32 channels
...  (u32 at 0x30 = 3) · Ogg Vorbis
```

### Script (`BurikoCompiledScriptVer1.00`)

```
char "BurikoCompiledScriptVer1.00\0" · u32 header_size · header (imported functions)
code:    u32 words. 03 <offset> pushes a string (offset from the start of the code)
strings: Shift-JIS, ending in \0
```

| Pattern | String |
|---------|--------|
| `03 <text> 140` | Text line |
| `03 <name> 03 <text> 140` | Name of the speaker |
| `03 <choice> 09 02` | Choice |
| other strings with Japanese characters | Lines spoken together, etc. |

ASCII strings are resource names (images, voices) and are not extracted. `7E <offset>` and `7F <offset>` name the function being called and the script; they are debug information.

To rebuild, the new strings are added at the end of the file and the `03` references are pointed at them. The English patch rewrote the scripts the same way: its scripts have the same code and the same strings in the same order, which is why they work as a reference line by line.

## License

Released under the [MIT License](../LICENSE). No game data is included; you need your own copy of the game. Please respect the original game developers and publishers.

## Credits

- [GARbro](https://github.com/morkt/GARbro) (MIT): reference for the ARC, BSE, DSC, CompressedBG and BGI image formats
- The script format, the audio header and the archive contents were checked against the game's own files
