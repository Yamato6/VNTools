# Yeti Tools

Tools for extracting the images of **CROSS†CHANNEL -Final Complete-** (PC, 2014), which runs on the **Yeti/Regista** engine. The PC release is a port of the Xbox 360 version, so its textures are still stored in the Xbox 360 format.

## Features

- **Graphical interface** (CustomTkinter) and command line
- **List** the contents of a `.cpk` archive (CRIWARE) and extract its files, already decompressed
- **Extract every image** of the game as PNG in one step: `.cpk` → CRILAYLA / Yeti LZSS → Yeti `pak` containers (nested) → XTX textures → PNG
- **Fast**: files are processed in parallel at low priority, so the PC stays responsive. `bg.cpk` (3,437 files, 1.8 GB) takes about 6 minutes with 8 processes
- **Resume**: an interrupted extraction continues where it stopped; cancelling never leaves half-written files
- **Loose XTX textures** such as `font48.xtx`, optionally split into one image per channel

## Files

| File | Description |
|------|-------------|
| `yeti_cpk.py`, `yeti_utf.py` | `.cpk` archives and their `@UTF` tables |
| `yeti_codecs.py`, `_crilayla.c` | CRILAYLA and Yeti LZSS decompression |
| `yeti_xtx.py` | Yeti `pak` containers and XTX textures |
| `yeti_extract.py` | Extraction logic (shared by the CLI and the GUI) |
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

- **Imágenes**: pick the game's `data` folder (or a single `.cpk`) and an output folder, then extract every image, with a live preview. Audio archives are skipped unless "Incluir audio" is checked.
- **Archivos CPK**: open a `.cpk`, browse its files and extract all of them or only the selected ones.
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
python yeti_tool.py images <data_folder|file.cpk> <output_folder> [--resume] [--all] [--workers N] [--png-level N]
```

**Example:**
```bash
python yeti_tool.py images "D:\Games\CROSS†CHANNEL FINAL COMPLETE\data" png --resume
```

Images are saved as `<output>\<cpk>\<id>_<entry>[_<subentry>].png`. Pak entries that are not textures are saved as `.bin`.

**Options:**
- `--resume`: skip the files already done. Finished IDs are listed in `<output>\<cpk>\_progreso.txt`
- `--all`: also process the audio archives (`bgm`, `bgs`, `se`, `vo`)
- `--workers N`: parallel processes (default: half of the CPU cores)
- `--png-level N`: PNG compression, 0-9 (default 1: fast, slightly larger files). The pixels are the same at every level

### Convert a loose XTX texture

```bash
python yeti_tool.py xtx <file.xtx> <output.png> [--split]
```

`--split` saves each channel as a separate grayscale image (`_a`, `_r`, `_g`, `_b`). `font48.xtx` stores a different sheet of glyphs in each 4-bit channel, in A, R, G, B order, so the split images are the readable ones.

## Game Files

| File | Contents |
|------|----------|
| `bg.cpk` | CG, backgrounds and sprites (3,437 files, CRILAYLA) |
| `sys.cpk` | Interface, buttons and gallery thumbnails (12 files, Yeti LZSS) |
| `bgm.cpk`, `bgs.cpk`, `se.cpk`, `vo.cpk` | Audio (not converted) |
| `font48.xtx` | Font texture (format 1) |
| `sn.bin` | Scripts (not supported) |

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

## License

Released under the [MIT License](../LICENSE). No game data or fonts are included; you need your own copy of the game. Please respect the original game developers and publishers.

## Credits

The code was written from the format descriptions; no code was copied from these projects. Format references:
- [wmltogether/cripaktools](https://github.com/wmltogether/cripaktools) and [uyjulian/CriPakTools](https://github.com/uyjulian/CriPakTools): CPK and CRILAYLA
- [mchubby/yetireg_tools](https://github.com/mchubby/yetireg_tools) and [WarpspeedSCP/yet-cc-rs](https://github.com/WarpspeedSCP/yet-cc-rs): Yeti LZSS
- [PyCriCodecs docs](https://github.com/Youjose/PyCriCodecs/wiki/Docs-and-Thoughts) and [The VG Resource Wiki](https://wiki.vg-resource.com/CPK): CPK modes
