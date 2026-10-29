# AdvHD Tools

Tools for extracting the images of **CROSS†CHANNEL** (WillPlus edition, 2012), which runs on the **AdvHD** engine by WillPlus. This is a different release from *CROSS†CHANNEL -Final Complete-*, which is handled by [Yeti Tools](../yeti-engine/).

## Features

- **Graphical interface** (CustomTkinter) and command line
- **List** the contents of a `.arc` archive and extract its files as they are
- **Extract every image** of the game as PNG in one step. Images and their opacity masks (`MSK`) are combined into PNG files **with transparency**
- Decodes the engine's own **WIPF** images (`WIP`, `MSK`, `MOS`), including animations and button states with up to 92 frames
- **Fast**: the whole game (about 1,600 images) takes under 20 seconds. Files are processed in parallel at low priority, so the PC stays responsive
- **Resume**: an interrupted extraction continues where it stopped; cancelling never leaves half-written files

## Files

| File | Description |
|------|-------------|
| `advhd_arc.py` | `.arc` archives |
| `advhd_wipf.py` | WIPF images and their LZSS compression |
| `advhd_extract.py` | Extraction logic (shared by the CLI and the GUI) |
| `advhd_tool.py` | Command line tool (opens the GUI when run without arguments) |
| `advhd_gui.py` | Graphical interface |
| `build_exe.bat` | Builds a standalone `.exe` with PyInstaller |

## Requirements

- Python 3.8+
- Pillow, NumPy
- CustomTkinter (GUI only)

```bash
pip install -r requirements.txt
```

## Graphical Interface

```bash
python advhd_gui.py
```

- **Imágenes**: pick the game folder (or a single `.arc`) and an output folder, then extract every image, with a live preview over a checkerboard to show transparency.
- **Archivos ARC**: open a `.arc`, filter its files by name or type and extract all of them or only the selected ones.

The default output folder is next to the game folder, never inside it: the tools only read the game files.

### Standalone executable

```bash
pip install -r requirements.txt
build_exe.bat
```

The result is `dist\AdvHD_Tools_GUI.exe`, which runs without Python installed.

## Usage

### List files

```bash
python advhd_tool.py list <file.arc>
```

### Extract raw files

```bash
python advhd_tool.py extract <file.arc> <output_folder>
```

Every entry is saved as `NAME.ext`, unchanged (`WIP` and `MSK` files stay in WIPF format).

### Extract all images

```bash
python advhd_tool.py images <game_folder|file.arc> <output_folder> [--resume] [--audio] [--no-alpha] [--workers N] [--png-level N]
```

**Example:**
```bash
python advhd_tool.py images "D:\Games\CROSS†CHANNEL" png --resume
```

**Options:**
- `--resume`: skip the images already done. Finished names are listed in `<output>\<arc>\_progreso.txt`
- `--audio`: also copy the audio (`Bgm`, `Se` and `Voice.arc`) as `.ogg`
- `--no-alpha`: do not apply the masks; save the image and `NAME_msk.png` separately
- `--workers N`: parallel processes (default: half of the CPU cores)
- `--png-level N`: PNG compression, 0-9 (default 1: fast, slightly larger files). The pixels are the same at every level

**Output** (`<output>\<arc>\`):

| Entries | Output |
|---------|--------|
| `PNG` + `MSK` | `NAME.png` with transparency |
| `PNG` alone | `NAME.png`, copied unchanged |
| `WIP` (+ `MSK`) | `NAME.png`, or `NAME_000.png`, `NAME_001.png`… for several frames |
| `MSK` alone | `NAME_msk.png` (transition and button masks) |
| `MOS` | `NAME_mos.png` (mosaic area of some CG) |
| `ANM`, `TBL`, `TXT`, `MAT` | `NAME.anm`… unchanged |
| `OGG` | `NAME.ogg`, only with `--audio` |
| `WSC` | skipped (scripts) |

`Rio.arc` (scripts only) is skipped. If a mask does not match its image, or is fully transparent (`CHATWND`), the image is saved opaque, the mask is saved separately and a warning is shown.

## Game Files

| File | Contents | Entries |
|------|----------|---------|
| `Chip.arc` | Event CG `EVCC*` and interface | 133 PNG, 134 MSK, 44 WIP, 25 MOS, 9 ANM, 9 TBL, 1 TXT |
| `Chip_B.arc` | Backgrounds `BGCC*` | 181 PNG + 181 MSK |
| `Chip_T.arc` | Character sprites `T*` | 399 PNG + 399 MSK |
| `Chip_S.arc` | Special illustrations `SGCC*` | 31 PNG + 31 MSK |
| `Chip_E.arc` | Effects `EFCC*` | 10 PNG + 10 MSK |
| `Chip_I.arc` | `I0034` | 1 PNG + 1 MSK |
| `Effect.arc` | Animated effects | 9 WIP, 9 MAT, 1 MSK |
| `Rio.arc` | Scripts | 324 WSC |
| `Bgm.arc`, `Se.arc`, `Voice.arc` | Audio | 26, 120 and 8,704 OGG |

Name suffixes: `A`/`B`/`C` are variants (pose or time of day); `S` is sepia (memories).

## File Formats

All values are little-endian.

### ARC archive

```
u32 type_count
type_count × { char ext[4] (ASCII, zero-padded); u32 file_count; u32 table_offset }
for each type, at table_offset: file_count × 21-byte entry:
    char name[13]  (Shift-JIS, null-terminated)
    u32  size
    u32  offset    (absolute)
```

No compression or encryption at the archive level. The same name can appear under several types (for example `TBYM0000` as `PNG` and as `MSK`).

### WIPF image

```
char  magic[4] = "WIPF"
u16   frame_count
u16   bpp            (8 or 24)
frame_count × { u32 width, height, x, y, unknown, compressed_size }
for each frame, in order:
    if bpp == 8: 1024-byte palette (256 × BGRA)
    compressed data (compressed_size bytes)
```

- **LZSS**: 0x1000-byte circular window filled with zeros, initial write position **1**. A flags byte is read and its 8 bits are processed from the lowest: bit 1 = literal byte; bit 0 = read `b0, b1`: window offset `(b0 << 4) | (b1 >> 4)`, length `(b1 & 0x0F) + 2`. Decompression stops at `width × height × bpp/8` bytes
- **8 bits**: one palette index per pixel. In masks the palette is a gray ramp, so the index is the opacity; a few masks (`CHATWND`, `RAIN`) use another gray scale, so the palette is applied
- **24 bits**: **planar**: the whole B plane, then G, then R
- **Frames**: the first is usually the full image; the others are placed at their `x, y` over it (button states, for example)

## License

Released under the [MIT License](../LICENSE). No game data is included; you need your own copy of the game. Please respect the original game developers and publishers.

## Credits

- [vn-tools/arc_unpacker](https://github.com/vn-tools/arc_unpacker): reference for the WillPlus formats
