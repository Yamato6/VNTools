# FVP Tools

Tools for extracting and repacking assets from the **Favorite View Point System** visual novel engine.

## Features

- **Graphical interface** (CustomTkinter) and command line
- **BIN Archive Extraction/Packing**: List, extract (all or a selection) and repack `.bin` archives
- **NVSG Image Conversion**: Convert proprietary NVSG images to PNG and back, with image preview
- **HCB Script Decompilation/Rebuilding**: Decompile game scripts for translation and rebuild them. **Translated lines can be longer than the original** (up to 254 bytes per line)
- **Custom font support**: A character table maps characters such as `á`, `ñ` or `¿` to the Shift-JIS characters your custom font draws them as
- **Automatic Format Detection**: Audio files (OGG/WAV) are automatically detected and given proper extensions
- **Batch Processing**: Convert entire folders of images at once

## Files

| File | Description |
|------|-------------|
| `fvp_bin.py`, `fvp_nvsg.py`, `fvp_hcb.py` | Format logic (shared by the CLI and the GUI) |
| `fvp_tools.py` | Command line tool (opens the GUI when run without arguments) |
| `fvp_gui.py` | Graphical interface |
| `charmap_es.txt` | Character table template for a custom font |
| `build_exe.bat` | Builds a standalone `.exe` with PyInstaller |

## Requirements

- Python 3.8+
- Pillow
- NumPy
- CustomTkinter (GUI only)

### Installation

```bash
pip install -r requirements.txt
```

## Graphical Interface

```bash
python fvp_gui.py
```

- **Archivos BIN**: open a `.bin`, filter its file list, preview images, extract everything or the selected files, and repack a folder.
- **Imágenes NVSG**: convert a folder of NVSG images to PNG (creates `decode_log.txt`) and back, with preview.
- **Scripts HCB**: extract strings (optionally decompile), rebuild with your translation and see a report of the lines that grew, were cut or had characters replaced.

### Standalone executable

```bash
pip install -r requirements.txt
build_exe.bat
```

The result is `dist\FVP_Tools_GUI.exe`, which runs without Python installed.

## Usage

### BIN Archive Operations

#### Extract files from a BIN archive

```bash
python fvp_tools.py bin-extract <file.bin> <output_folder> [--no-ext]
```

**Examples:**
```bash
# Extract with automatic extension detection (recommended for audio)
python fvp_tools.py bin-extract bgm.bin bgm_output/

# Extract without adding extensions (for NVSG image files)
python fvp_tools.py bin-extract graph_bg.bin images_output/ --no-ext
```

**Options:**
- `--no-ext`: Do not add file extensions. Use this when extracting NVSG images that need to be repacked later.

Extraction also writes `_bininfo.json` with the original internal names, so a folder extracted with extensions can still be repacked with the right names. It is never packed.

#### Pack files into a BIN archive

```bash
python fvp_tools.py bin-pack <input_folder> <output.bin>
```

**Example:**
```bash
python fvp_tools.py bin-pack modified_images/ graph_bg_modified.bin
```

**Note:** Files must maintain their original naming format with numeric prefixes (e.g., `0000_filename`, `0001_filename`).

---

### NVSG Image Operations

NVSG is the proprietary image format used by the FVP engine. Images are compressed with zlib and stored without file extensions.

#### Decode a single NVSG to PNG

```bash
python fvp_tools.py nvsg-decode <nvsg_file> <output.png>
```

**Example:**
```bash
python fvp_tools.py nvsg-decode 0000_BG001_000 background.png
```

#### Encode a PNG to NVSG

```bash
python fvp_tools.py nvsg-encode <input.png> <output_nvsg> --x <N> --y <N> [--count <N>]
```

**Parameters:**
- `--x`: X offset for sprite positioning (from original decode log)
- `--y`: Y offset for sprite positioning (from original decode log)
- `--count`: Number of frames for animated sprites (default: 1)

**Example:**
```bash
python fvp_tools.py nvsg-encode background.png 0000_BG001_000 --x 0 --y 0
```

---

### Batch Image Conversion

#### Decode all NVSG files in a folder

```bash
python fvp_tools.py batch-decode <nvsg_folder> <png_folder>
```

This command:
1. Converts all NVSG files to PNG
2. Creates a `decode_log.txt` with metadata for each image

**Example:**
```bash
python fvp_tools.py batch-decode extracted_images/ png_output/
```

#### Encode all PNG files back to NVSG

```bash
python fvp_tools.py batch-encode <png_folder> <nvsg_folder> <decode_log.txt>
```

This command uses the metadata from `decode_log.txt` to properly re-encode the images.

**Example:**
```bash
python fvp_tools.py batch-encode png_output/ nvsg_output/ png_output/decode_log.txt
```

---

### HCB Script Operations

HCB files contain the game's compiled script bytecode, including all dialogue text, character names, and game logic.

#### Decompile an HCB file

```bash
python fvp_tools.py hcb-decode <script.hcb> <output.txt> [--strings strings.txt]
```

**Example:**
```bash
python fvp_tools.py hcb-decode Hoshimemo_HD.hcb script.txt --strings strings.txt
```

This creates:
- `script.txt` - Decompiled bytecode with opcodes and labels
- `strings.txt` - Extracted strings in translation-friendly format

#### Extract only strings (faster)

```bash
python fvp_tools.py hcb-strings <script.hcb> <strings.txt>
```

**Example:**
```bash
python fvp_tools.py hcb-strings Hoshimemo_HD.hcb strings.txt
```

#### Rebuild HCB with modified strings

```bash
python fvp_tools.py hcb-rebuild <original.hcb> <strings.txt> <output.hcb> [--charmap <map.txt>] [--fixed]
```

**Example:**
```bash
python fvp_tools.py hcb-rebuild Hoshimemo_HD.hcb translated_strings.txt Hoshimemo_HD_new.hcb --charmap charmap_es.txt
```

**How longer lines work:** `call`, `jmp` and `jmpcond` use absolute addresses, so a string that grows shifts every instruction after it. The rebuild writes each string at its real length and then relocates every reference into the code: jumps, calls, function pointers passed to `ThreadStart` (and other functions), and the entry point stored in the data section. If a reference cannot be relocated safely, the rebuild stops with an error instead of writing a broken script.

`--fixed` restores the old behavior (every string keeps its original size: padded with spaces if shorter, cut if longer), in case a game does not accept a relocated script.

**Limitations:**
- Each string can hold up to **254 bytes** (its length is stored in 1 byte): 254 half-width characters or 127 full-width ones. Longer strings are cut at a character boundary and listed in the report
- Only **Shift-JIS (CP932)** encoding is supported by the game engine
- Characters not in Shift-JIS (ñ, á, ü, emojis, etc.) keep the original line unless a character table maps them (see below)
- Saved games may store addresses of script functions; old saves may not work with a rebuilt script

#### Character table (custom fonts)

If your game uses a custom font that draws some Shift-JIS characters (for example half-width katakana) as `á`, `ñ`, `¿`..., write the translation normally and pass a table with `--charmap`. One pair per line, `#` for comments:

```
á=ｱ
ñ=ｶ
¿=ｸ
```

When rebuilding, every `á` is written as `ｱ`. To read an already translated script, extract it with `--charmap <map.txt> --reverse`. This is off by default because the original Japanese text uses those same characters.

#### Strings file format

```
ID|ADDRESS|TEXT
```

**Example:**
```
4921|0x0009A2FE|I loved her.
4922|0x0009A315|I loved her.
4923|0x0009A336|It was a sort of love that wasn't quite romantic.
```

- Lines starting with `#` are comments
- Use `\n` for newlines, `\\` for backslash
- The `~` character is used for in-game line breaks

#### Split strings into parts (for large projects)

For large translation projects, you can split the strings file into smaller parts:

```bash
python fvp_tools.py hcb-split <strings.txt>
```

First, add `<part>` tags to your strings file:

```
<part name="Prologue" filename="parts/prologue.txt">
0001|0x00001234|First line of prologue
0002|0x00001256|Second line
</part>
<part name="Chapter 1" filename="parts/chapter1.txt">
0003|0x00001280|Chapter 1 starts here
...
</part>
```

Then run `hcb-split` to create the individual files.

#### Merge parts back together

To rebuild from split files, create a build script:

```
# build.txt
<part filename="parts/prologue.txt">
<part filename="parts/chapter1.txt">
<part filename="parts/chapter2.txt">
```

Or simply list files (one per line):
```
# build.txt
parts/prologue.txt
parts/chapter1.txt
parts/chapter2.txt
```

Then merge:
```bash
python fvp_tools.py hcb-merge build.txt merged_strings.txt
python fvp_tools.py hcb-rebuild original.hcb merged_strings.txt output.hcb
```

---

## Complete Workflow Example

### Extracting and modifying background images:

```bash
# 1. Extract the BIN archive (NVSG files have no extension)
python fvp_tools.py bin-extract graph_bg.bin extracted/

# 2. Convert all NVSG to PNG for editing
python fvp_tools.py batch-decode extracted/ png_images/

# 3. Edit the PNG files with your image editor...

# 4. Convert modified PNGs back to NVSG
python fvp_tools.py batch-encode png_images/ modified_nvsg/ png_images/decode_log.txt

# 5. Repack into a new BIN archive
python fvp_tools.py bin-pack modified_nvsg/ graph_bg.bin
```

### Extracting audio files:

```bash
# Audio files are automatically detected and given .ogg or .wav extensions
python fvp_tools.py bin-extract bgm.bin music/
python fvp_tools.py bin-extract voice.bin voices/
python fvp_tools.py bin-extract se.bin sound_effects/
```

---

## File Formats

### BIN Archive Structure

```
+----------------------------------+
| Header (8 bytes)                 |
|   - file_count (4 bytes, U32LE)  |
|   - names_size (4 bytes, U32LE)  |
+----------------------------------+
| File Table (12 bytes x N)        |
|   For each file:                 |
|   - name_offset (4 bytes)        |
|   - file_offset (4 bytes)        |
|   - file_size (4 bytes)          |
+----------------------------------+
| Filenames (Shift_JIS, null-term) |
+----------------------------------+
| File Data                        |
+----------------------------------+
```

### NVSG Image Structure

```
+----------------------------------+
| hzc1 Header (12 bytes)           |
|   - magic "hzc1" (4 bytes)       |
|   - uncompressed_size (4 bytes)  |
|   - header_size (4 bytes) = 0x20 |
+----------------------------------+
| NVSG Header (32 bytes)           |
|   - magic "NVSG" (4 bytes)       |
|   - always_256 (2 bytes)         |
|   - format (2 bytes): 0-3        |
|   - width (2 bytes)              |
|   - height (2 bytes)             |
|   - x_offset (2 bytes)           |
|   - y_offset (2 bytes)           |
|   - unknown (4 bytes)            |
|   - image_count (4 bytes)        |
|   - reserved (8 bytes)           |
+----------------------------------+
| Compressed Data (zlib)           |
|   Pixels in BGR/BGRA format      |
+----------------------------------+
```

### NVSG Image Formats

| Format | Description | Bits per pixel |
|--------|-------------|----------------|
| 0 | BGR (no alpha) | 24 |
| 1 | BGRA (with alpha) | 32 |
| 2 | BGRA with multiple frames | 32 |
| 3 | Grayscale | 8 |

---

## Supported Content Types

| Archive | Content Type | Auto Extension |
|---------|--------------|----------------|
| `bgm.bin`, `bgm2.bin` | Background Music | `.ogg` |
| `voice.bin`, `voice2.bin` | Voice Lines | `.ogg` |
| `se.bin`, `se_env.bin`, `se_sys.bin` | Sound Effects | `.wav` |
| `graph_bg.bin` | Backgrounds | (none) |
| `graph_vis*.bin` | Character Sprites | (none) |

---

## Important Notes

1. **NVSG files have no extension**: This is a requirement of the FVP engine. Do not add extensions to image files.

2. **Preserve the decode log**: The `decode_log.txt` file contains essential metadata (x, y offsets, frame count) needed for proper re-encoding.

3. **Filename prefixes**: Keep the numeric prefixes (e.g., `0000_`, `0001_`) when modifying files. They ensure correct ordering when repacking.

4. **Character encoding**: Filenames use Shift_JIS encoding for Japanese characters.

---

## Troubleshooting

### "Not a valid NVSG file" error
- The file may not be an NVSG image (could be audio or other format)
- Try extracting with `bin-extract` without `--no-ext` to auto-detect the format

### Images look wrong after re-encoding
- Make sure you're using the correct x, y, and count values from the decode log
- Verify the PNG hasn't changed dimensions

### Repacked BIN doesn't work in game
- Ensure all files are present and in the correct order
- Check that NVSG files have no extension
- Verify filenames match the originals (without the numeric prefix)

---

## HCB Script File Format

HCB files contain the compiled bytecode for the game's script, including:
- Dialogue text
- Character names
- Scene flow and branching logic
- Function calls and game commands

### HCB Structure

```
+----------------------------------+
| Header (4 bytes, U32LE)          |
|   Offset to data section         |
+----------------------------------+
| Code Section                     |
|   Bytecode instructions          |
|   Inline strings (opcode 0x0E)   |
+----------------------------------+
| Data Section (sysdesc)           |
|   Entry point (U32LE, address of |
|   the main function)             |
|   Global variable counts         |
|   Game title                     |
|   Syscall table                  |
+----------------------------------+
```

### String Instruction Format

```
+----------------------------------+
| Opcode 0x0E (1 byte)             |
| String Length (1 byte)           |
| String Data (N bytes, Shift-JIS) |
| Null Terminator (included in len)|
+----------------------------------+
```

### HCB Opcodes

The script uses a stack-based virtual machine with these opcodes:

| Opcode | Name | Description |
|--------|------|-------------|
| 0x00 | nop | No operation |
| 0x01 | initstack | Initialize function stack |
| 0x02 | call | Call function |
| 0x03 | syscall | System call |
| 0x04 | ret | Return from function |
| 0x06 | jmp | Unconditional jump |
| 0x07 | jmpcond | Conditional jump |
| 0x08 | pushtrue | Push true to stack |
| 0x09 | pushfalse | Push false to stack |
| 0x0A-0C | pushint | Push integer (32/16/8 bit) |
| 0x0E | pushstring | Push string to stack |
| 0x0F | pushglobal | Push global variable |
| 0x10 | pushstack | Push stack variable |
| 0x15 | popglobal | Pop to global variable |
| 0x19 | neg | Negate |
| 0x1A-1E | add/sub/mul/div/mod | Arithmetic operations |
| 0x20-21 | logand/logor | Logical AND/OR |
| 0x22-27 | eq/neq/gt/le/lt/ge | Comparison operations |

### HCB Script Syntax

**Labels:**
```
function_name:
    initstack 2 0
    ...
    ret
```

**Comments:**
```
# This is a comment
```

**Function pointers:**
```
pushint LABEL:function_XXX_
syscall ThreadStart
```

**Text strings:**
```
pushstring Hello, world!
```

### HCB Translation Workflow

```bash
# 1. Extract strings from the original script
python fvp_tools.py hcb-decode script.hcb decompiled.txt --strings strings.txt

# 2. Edit strings.txt with your translations
# Keep the ID|ADDRESS|TEXT format
# Example: 4921|0x0009A2FE|La amaba.

# 3. Rebuild with translated strings
python fvp_tools.py hcb-rebuild script.hcb strings.txt script_translated.hcb

# 4. Replace the original HCB in the game folder
# Make a backup first!
```

**Tips for translation:**
- Translations can be longer than the original, up to 254 bytes per line; check the rebuild report for cut lines
- Only translate dialogue and names: many strings are file names or engine commands and must stay unchanged
- The `~` character creates line breaks in dialogue
- Test frequently in-game: the text box itself may not fit very long lines

### Supported Games

The HCB tool has been tested with:
- Irotoridori no Sekai
- Astral Air
- Hoshizora no Memoria (Fan Disc)

---

## License

This tool is provided for personal and educational use. Please respect the copyrights of the original game content.

## Credits

- **fvp_tools.py**: Python implementation for BIN/NVSG/HCB handling
- **HCB format research**: binaryfail, akerou (original C++ tool)