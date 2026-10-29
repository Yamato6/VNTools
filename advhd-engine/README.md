# AdvHD Tools

Tools for translating **CROSS†CHANNEL** (WillPlus edition, 2012), which runs on the **AdvHD** engine by WillPlus: extract and rebuild the scripts, extract and repack the images, extract the audio and repack any `.arc` archive. This is a different release from *CROSS†CHANNEL -Final Complete-*, which is handled by [Yeti Tools](../yeti-engine/).

## Features

- **Graphical interface** (CustomTkinter) and command line
- **List** the contents of a `.arc` archive, extract its files as they are and **pack** a folder back into a `.arc` (byte-identical when nothing changed)
- **Scripts**: extract the text of the 324 scripts (`Rio.arc`) for translation and rebuild them. **Translated lines can be longer than the original**: every jump and conditional block in the scripts is relocated
- **Repack images**: edit the extracted PNG files and build a new `.arc`. Only the edited images are re-encoded (PNG + mask, WIPF), the rest is copied unchanged
- **Audio**: extract the music, sound effects and voices as `.ogg`
- **Extract every image** of the game as PNG in one step. Images and their opacity masks (`MSK`) are combined into PNG files **with transparency**
- Decodes the engine's own **WIPF** images (`WIP`, `MSK`, `MOS`), including animations and button states with up to 92 frames
- **Fast**: the whole game (about 1,600 images) takes under 20 seconds. Files are processed in parallel at low priority, so the PC stays responsive
- **Resume**: an interrupted extraction continues where it stopped; cancelling never leaves half-written files

## Files

| File | Description |
|------|-------------|
| `advhd_arc.py` | `.arc` archives |
| `advhd_wipf.py` | WIPF images and their LZSS compression (decoding and encoding) |
| `advhd_wsc.py` | WSC scripts: text extraction and rebuilding |
| `advhd_extract.py` | Image and audio extraction and image repacking (shared by the CLI and the GUI) |
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
- **Reempaquetar**: pick the original `.arc`, the folder with the extracted (and edited) images and the output `.arc`.
- **Guiones**: extract the text of `Rio.arc` (one file per script) and rebuild it with your translation, with a report of the lines that grew, were cut or could not be encoded.
- **Audio**: extract `Bgm.arc`, `Se.arc` and `Voice.arc` as `.ogg`.
- **Archivos ARC**: open a `.arc`, filter its files by name or type and extract all of them or only the selected ones. "Empaquetar carpeta…" packs a folder back into a `.arc`.

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

### Repack images

```bash
python advhd_tool.py images-pack <original.arc> <images_folder> <output.arc> [--allow-resize]
```

`images_folder` is the output of `images` (or the folder of that `.arc` inside it). Extraction writes `_images.json` with a hash of every PNG, so only the images you edited are re-encoded:

- `NAME.png` with transparency → the `PNG` entry (RGB) and its `MSK` mask (alpha)
- Frames `NAME_000.png`… → the `WIP` image with all its frames, keeping each frame's position
- `NAME_msk.png`, `NAME_mos.png` → the `MSK` or `MOS` entry

Images must keep their original size unless `--allow-resize` is given. If any image cannot be encoded, no file is written and the errors are listed.

**Example:**
```bash
python advhd_tool.py images "D:\Games\CROSS†CHANNEL" png
# edit png\Chip_T\TBYM0000.png…
python advhd_tool.py images-pack Chip_T.arc png Chip_T_new.arc
```

### Pack a folder

```bash
python advhd_tool.py pack <folder> <output.arc>
```

Packs every `NAME.ext` file of a folder (for example one extracted with `extract` or `audio`). Types and names are sorted the same way as in the game's archives, so an unchanged folder gives an identical `.arc`. Names can be up to 12 bytes in Shift-JIS.

### Audio

```bash
python advhd_tool.py audio <game_folder> <output_folder>
```

Copies the `.ogg` files of `Bgm.arc`, `Se.arc` and `Voice.arc` to `<output>\<arc>\`.

### Scripts

```bash
python advhd_tool.py wsc-strings <Rio.arc> <text_folder> [--listing] [--charmap <map.txt> --reverse]
python advhd_tool.py wsc-rebuild <Rio.arc> <text_folder> <new_Rio.arc> [--charmap <map.txt>] [--fixed]
```

`wsc-strings` writes one file per script (`CCA0001.txt`…) with the messages, character names, choices and scene titles:

```
# título
0000|0x00000005|CROSS†CHANNEL
0001|0x000000B4|最古の記憶は。%K%P
…
# nombre
0013|0x00000356|少女
0014|0x0000035B|「……太一」%K%P
```

Translate only the text after the second `|`. Keep the control codes: `%K%P` waits for a click and ends the message, and `{漢字:よみ}` is ruby (reading) text. `--listing` also writes the full disassembly of every script to `listado\`.

`wsc-rebuild` takes the original `Rio.arc` and the folder with the translated files (scripts without a file are copied unchanged):

- **Longer lines:** each text is written at its real length, then every absolute jump (`0x06`) and every conditional block size (`0x01`) is relocated. Rebuilding with unchanged text gives an identical `Rio.arc`
- `--fixed`: every text keeps its original size (padded with spaces or cut), in case the game does not accept a relocated script
- Only **Shift-JIS (CP932)** text is supported. Characters outside it (ñ, á, ¿…) keep the original line and are listed in the report, unless a character table maps them (`--charmap`, one `á=ｱ` pair per line, same format as FVP Tools) to characters a custom font draws as them
- Saved games store positions in the scripts; old saves may not work with rebuilt scripts

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

### WSC script

Every byte is rotated 2 bits to the left (decrypt by rotating right). Decrypted, a script is a sequence of instructions: a 1-byte opcode and its arguments (little-endian), ending with `0xFF` and a 9-byte trailer.

| Opcode | Arguments | Meaning |
|--------|-----------|---------|
| `0x41` | u16 id, u8, u8, text | Message |
| `0x42` | u16 id, u8, u8, u8, name, text | Message with a character name |
| `0x02` | u16 count, count × (u16 id, text, 11 bytes) | Choice menu |
| `0xE0` | text | Scene title |
| `0x06` | u32 target, u8 | Jump to an absolute offset |
| `0x01` | u8, u16, u16, u32 size, u8 | Condition: skips the next `size` bytes |

Texts are Shift-JIS and null-terminated. The tool knows the argument layout of all 60 opcodes used by the game, worked out from the 324 scripts: every script is parsed from start to end with no bytes left over, and every jump and block ends at the start of an instruction.

## License

Released under the [MIT License](../LICENSE). No game data is included; you need your own copy of the game. Please respect the original game developers and publishers.

## Credits

- [vn-tools/arc_unpacker](https://github.com/vn-tools/arc_unpacker): reference for the WillPlus formats
- The WSC opcode table was worked out from the game's own scripts
