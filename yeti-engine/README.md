# Yeti Tools

Tools for translating **CROSS†CHANNEL -Final Complete-** (PC, 2014), which runs on the **Yeti/Regista** engine: extract and rebuild the scripts, extract and repack the images, extract the audio and repack any `.cpk` archive. The PC release is a port of the Xbox 360 version, so its textures are still stored in the Xbox 360 format.

## Features

- **Graphical interface** (CustomTkinter) and command line
- **List** the contents of a `.cpk` archive (CRIWARE) and extract its files, already decompressed
- **Extract every image** of the game as PNG in one step: `.cpk` → CRILAYLA / Yeti LZSS → Yeti `pak` containers (nested) → XTX textures → PNG
- **Fast**: files are processed in parallel at low priority, so the PC stays responsive. `bg.cpk` (3,437 files, 1.8 GB) takes about 6 minutes with 8 processes
- **Resume**: an interrupted extraction continues where it stopped; cancelling never leaves half-written files
- **Loose XTX textures** such as `font48.xtx`, optionally split into one image per channel
- **Repack images**: edit the extracted PNG files and build a new `.cpk`. Only the edited textures are re-encoded, in their original format (ARGB8888, ARGB4444 or DXT5, keeping the untouched DXT5 blocks); the rest is copied unchanged
- **Scripts**: extract the text of the 352 scripts in `sn.bin` for translation and rebuild it. **Translated lines can be longer than the original**: every jump, switch and choice target is relocated
- **Audio**: extract the music, sound effects and voices as `.hca`, and as `.wav` if [vgmstream](https://vgmstream.org) is available
- **Repack files**: put edited `<id>.bin` or `<id>.hca` files back into a `.cpk`. An unchanged folder gives a byte-identical `.cpk`

## Files

| File | Description |
|------|-------------|
| `yeti_cpk.py`, `yeti_utf.py` | `.cpk` archives and their `@UTF` tables (reading and writing) |
| `yeti_codecs.py`, `_crilayla.c` | CRILAYLA decompression, Yeti LZSS compression and decompression |
| `yeti_xtx.py` | Yeti `pak` containers and XTX textures (decoding and encoding) |
| `yeti_script.py` | `sn.bin` scripts: text extraction and rebuilding |
| `yeti_extract.py` | Image and audio extraction and repacking (shared by the CLI and the GUI) |
| `yeti_tool.py` | Command line tool (opens the GUI when run without arguments) |
| `yeti_gui.py` | Graphical interface |
| `build_exe.bat` | Builds `_crilayla.dll` and a standalone `.exe` with PyInstaller |

## Requirements

- Python 3.8+
- Pillow, NumPy
- CustomTkinter (GUI only)
- Optional: `gcc` to build `_crilayla.dll`. Without it, CRILAYLA falls back to pure Python, about 100 times slower (roughly an extra hour for `bg.cpk`)

```bash
pip install -r requirements.txt
gcc -O2 -shared -static-libgcc -o _crilayla.dll _crilayla.c
```

On Linux, build `_crilayla.so` instead (`gcc -O2 -shared -fPIC -o _crilayla.so _crilayla.c`).

## Graphical Interface

```bash
python yeti_gui.py
```

- **Imágenes**: pick the game's `data` folder (or a single `.cpk`) and an output folder, then extract every image, with a live preview.
- **Reempaquetar**: pick the original `.cpk`, the folder with the extracted (and edited) images and the output `.cpk`.
- **Guiones**: extract the text of `sn.bin` (one file per script) and rebuild it with your translation, with a report of the lines that grew, were cut or could not be encoded.
- **Audio**: extract `bgm.cpk`, `se.cpk` and `vo.cpk` as `.hca` (and `.wav` with vgmstream).
- **Archivos CPK**: open a `.cpk`, browse its files and extract all of them or only the selected ones. "Reempaquetar carpeta…" puts edited `<id>.bin` / `<id>.hca` files back into it.
- **XTX**: convert a loose `.xtx` texture to PNG, with preview.

The default output folder is next to `data`, never inside it: the tools only read the game files.

### Standalone executable

```bash
pip install -r requirements.txt
build_exe.bat
```

The result is `dist\Yeti_Tools_GUI.exe`, which runs without Python installed. If `gcc` is available, `_crilayla.dll` is built and bundled into the executable.

## Usage

### List files

```bash
python yeti_tool.py list <file.cpk>
```

### Extract raw files

```bash
python yeti_tool.py extract <file.cpk> <output_folder>
```

Every file is saved as `<id>.bin`, already decompressed. The archives have no file names, only numeric IDs.

### Extract all images

```bash
python yeti_tool.py images <data_folder|file.cpk> <output_folder> [--resume] [--workers N] [--png-level N]
```

**Example:**
```bash
python yeti_tool.py images "D:\Games\CROSS†CHANNEL FINAL COMPLETE\data" png --resume
```

Images are saved as `<output>\<cpk>\<id>_<entry>[_<subentry>].png`. Pak entries that are not textures are saved as `.bin`.

**Options:**
- `--resume`: skip the files already done. Finished IDs are listed in `<output>\<cpk>\_progreso.txt`
- `--workers N`: parallel processes (default: half of the CPU cores)
- `--png-level N`: PNG compression, 0-9 (default 1: fast, slightly larger files). The pixels are the same at every level

### Convert a loose XTX texture

```bash
python yeti_tool.py xtx <file.xtx> <output.png> [--split]
```

`--split` saves each channel as a separate grayscale image (`_a`, `_r`, `_g`, `_b`). `font48.xtx` stores a different sheet of glyphs in each 4-bit channel, in A, R, G, B order, so the split images are the readable ones.

### Repack images

```bash
python yeti_tool.py images-pack <original.cpk> <images_folder> <output.cpk>
```

`images_folder` is the output of `images` (or the folder of that `.cpk` inside it). Extraction writes `_images.json` with a hash of every PNG, so only the textures you edited are re-encoded, in the original format and size of each texture. The files that contain them are stored without CRILAYLA (the game reads both), and with Yeti LZSS again if the original used it (`sys.cpk`). Everything else is copied unchanged.

**Example:**
```bash
python yeti_tool.py images data png
# edit png\bg\00012_000.png…
python yeti_tool.py images-pack data\bg.cpk png data_new\bg.cpk
```

### Repack files

```bash
python yeti_tool.py pack <original.cpk> <folder> <output.cpk>
```

Replaces the files of `folder` named `<id>.bin` (from `extract`, decompressed) or `<id>.hca` (from `audio`) that differ from the original. `.bin` files that were Yeti LZSS compressed in the original are compressed again.

### Audio

```bash
python yeti_tool.py audio <data_folder> <output_folder> [--no-wav]
```

Saves `bgm.cpk`, `se.cpk` and `vo.cpk` as `<output>\<cpk>\<id>.hca` (CRIWARE HCA, not encrypted). If `vgmstream-cli` is in the `PATH` or next to the tool, each file is also converted to `.wav`. To replace audio, encode new `.hca` files with your own tools, name them like the originals and use `pack`.

### Scripts

```bash
python yeti_tool.py script-strings <sn.bin> <text_folder> [--listing] [--charmap <map.txt> --reverse]
python yeti_tool.py script-rebuild <sn.bin> <text_folder> <new_sn.bin> [--charmap <map.txt>] [--fixed]
```

`script-strings` writes one file per script (`005.txt`…) with the messages, character names and choices:

```
# nombre
0000|0x00000153|霧
0001|0x00000161|「いきなり窓の外にいるなんて、もう二度としないでください」
```

Translate only the text after the second `|`. `--listing` also writes the full disassembly of every script to `listado\`.

`script-rebuild` takes the original `sn.bin` and the folder with the translated files (scripts without a file are copied unchanged):

- **Longer lines:** each text is written at its real length and every jump, switch arm and choice target is relocated. Rebuilding with unchanged text gives identical scripts, and a rebuilt `sn.bin` can be rebuilt again
- Script `000` (the developer debug menu) is always rebuilt with fixed sizes
- `--fixed`: every text keeps its original size (padded with spaces or cut)
- Only **Shift-JIS (CP932)** text is supported. Characters outside it (ñ, á, ¿…) keep the original line and are listed in the report, unless a character table maps them (`--charmap`, one `á=ｱ` pair per line, same format as FVP Tools). The game font is `font48.xtx`
- Saved games store positions in the scripts; old saves may not work with rebuilt scripts

## Game Files

| File | Contents |
|------|----------|
| `bg.cpk` | CG, backgrounds and sprites (3,437 files, CRILAYLA) |
| `bgs.cpk` | 320×240 images (3,437 files, CRILAYLA) |
| `sys.cpk` | Interface, buttons and gallery thumbnails (12 files, Yeti LZSS) |
| `bgm.cpk`, `se.cpk`, `vo.cpk` | Music, sound effects and voices (HCA) |
| `font48.xtx` | Font texture (format 1) |
| `sn.bin` | Scripts (Yeti LZSS) |

## File Formats

### CPK archive (CRIWARE)

- `CPK ` signature at 0x00. Each table: 4-byte signature, `int32` (0xFF), `int64` size (little-endian), then an `@UTF` table (big-endian)
- Both image archives use `CpkMode = 0`: no TOC, only an **ITOC** (`ItocOffset = 0x800`, `ContentOffset = 0x1000`, `Align = 2048`). The tables are not encrypted
- ITOC: row 0 holds the `DataL` and `DataH` columns, blobs that are `@UTF` tables with `ID`, `FileSize`, `ExtractSize`. Files have no names; they are sorted by ID and each offset is the sum of the previous `FileSize` values, rounded up to `Align`, starting at `ContentOffset`

### @UTF table

| Field | Type |
|------|------|
| `@UTF` | signature |
| `table_size` | u32 |
| `rows_offset`, `strings_offset`, `data_offset`, `table_name` | u32, relative to 8 bytes after the signature |
| `num_columns`, `row_length` | u16 |
| `num_rows` | u32 |

Each column is 1 flags byte + a u32 offset to its name. High nibble: `0x10` zero, `0x30` constant, `0x50` per row. Low nibble: `0-7` integers (u8, u16, u32, u64), `8` float, `0xA` string, `0xB` blob (offset + size). Encrypted tables use an XOR stream: `m = 0x655F`, `t = 0x4115`, `byte ^= m & 0xFF; m *= t`.

### Compression

- **CRILAYLA** (`bg.cpk`): `CRILAYLA`, u32 decompressed size, u32 offset of 0x100 uncompressed bytes that go at the start of the output. The bitstream is read backwards
- **Yeti LZSS** (`sys.cpk`, `ExtractSize == FileSize`): u32 decompressed size + blocks with a flags byte (bit 1 = literal; bit 0 = u16 reference with length `3 + ((info & 0x0F00) >> 8)` and distance `((info & 0xF000) >> 4) | (info & 0xFF)`, 4 KB window)

### Yeti pak container

- Table of 16-byte entries: `u32 offset, u32 size, u32 0, u32 0` (little-endian)
- The first offset is the table size, so `count = first_u32 / 16`
- Entries with offset and size 0 are empty; entries can be nested paks

### XTX texture

32-byte header: `xtx\0`, u8 format (+3 padding), then four **big-endian** u32: aligned width, aligned height, real width, real height. Pixel data starts at 0x20 in Xbox 360 tiled order (`XGAddress2DTiledX/Y`); the image is cropped to the real size after untiling.

| Format | Pixel | Used by | Decoding |
|--------|-------|---------|----------|
| 0 | ARGB8888, 4 B/px | CG and sprites in `bg.cpk` | u32 big-endian, untile with pitch 4 |
| 1 | ARGB4444, 2 B/px | Text windows, `font48.xtx` | u16 big-endian, untile with pitch 2 |
| 2 | DXT5 | Most of `sys.cpk` | swap bytes in pairs, untile 4×4 blocks with pitch 16, decode BC3 |

### sn.bin scripts

`sn.bin` is Yeti LZSS compressed. Decompressed, it is a pak with 353 entries: 352 scripts and, last, a table with the 16-byte trailer of every script.

Each script starts with a u32 code offset (almost always 4). The code is a sequence of instructions (1-byte opcode and little-endian arguments) that ends with `0x05`, followed by the 16-byte trailer and zero padding to a multiple of 16.

| Opcode | Arguments | Meaning |
|--------|-----------|---------|
| `0x45`, `0x86` | 4 bytes, text | Message |
| `0x47` | u16 (0x0D: text follows; otherwise another u16), text | Character name |
| `0x31`, `0x32` | u8, u8, u8 count, 3 bytes, count × (6 bytes, u32 target, text) | Choices |
| `0x01`, `0x03` | u32 target | Jump |
| `0x06`–`0x0B` | 4 bytes, u32 target | Conditional jump |
| `0x0C`, `0x0D` | 2 bytes, u32 target | Conditional jump |
| `0x0E` | u16, u16 count, count × (u16, u32 target) | Switch |

Targets are absolute offsets in the script (0 means none). Texts are Shift-JIS and null-terminated. The argument sizes of every opcode were checked against all 352 scripts: each one is parsed to its end and every target falls on the start of an instruction.

## License

Released under the [MIT License](../LICENSE). No game data or fonts are included; you need your own copy of the game. Please respect the original game developers and publishers.

## Credits

The code was written from the format descriptions; no code was copied from these projects. Format references:
- [WarpspeedSCP/yet-cc-rs](https://github.com/WarpspeedSCP/yet-cc-rs): opcode argument sizes of the scripts, checked here against the game files
- [wmltogether/cripaktools](https://github.com/wmltogether/cripaktools) and [uyjulian/CriPakTools](https://github.com/uyjulian/CriPakTools): CPK and CRILAYLA
- [mchubby/yetireg_tools](https://github.com/mchubby/yetireg_tools) and [WarpspeedSCP/yet-cc-rs](https://github.com/WarpspeedSCP/yet-cc-rs): Yeti LZSS
- [PyCriCodecs docs](https://github.com/Youjose/PyCriCodecs/wiki/Docs-and-Thoughts) and [The VG Resource Wiki](https://wiki.vg-resource.com/CPK): CPK modes
