# CrossChannel MultiTool

A Python tool for extracting and repacking `.PD` archive files used by the **FS Engine** in the visual novel **CROSS†CHANNEL**.

## Features

- **Graphical interface** (CustomTkinter) and command line
- **List** the contents of a `.PD` archive and extract all files or only a selection
- **Repack** files into new `.PD` archives, keeping the original mode and file order
- Supports both `PackOnly` and `PackPlus` formats, with automatic mode detection
- Automatic XOR decryption/encryption for `PackPlus` archives
- Preserves original file structure, including subfolders

## Files

| File | Description |
|------|-------------|
| `pd_core.py` | `.PD` format logic (shared by the CLI and the GUI) |
| `crosschannel_tool.py` | Command line tool (opens the GUI when run without arguments) |
| `crosschannel_gui.py` | Graphical interface |
| `build_exe.bat` | Builds a standalone `.exe` with PyInstaller |

## Requirements

- Python 3.8 or higher
- Command line: no external dependencies
- GUI: `customtkinter` (`pip install -r requirements.txt`)

## Graphical Interface

```bash
python crosschannel_gui.py
```

- **Extraer** tab: open a `.PD`, browse/filter its file list, and extract everything or only the selected files.
- **Empaquetar** tab: pick a folder and an output `.PD`. In `Auto` mode the archive mode is taken from `_pdinfo.json`.
- Progress bar with a **Cancelar** button; cancelling never leaves a half-written `.PD` behind (an existing file is kept intact).

### Standalone executable

```bash
pip install -r requirements.txt
build_exe.bat
```

The result is `dist\CrossChannel_MultiTool_GUI.exe`, which runs without Python installed.

## Usage

### List Files

```bash
python crosschannel_tool.py list <archive.PD>
```

### Extract Files

```bash
python crosschannel_tool.py extract <archive.PD> <output_directory>
```

**Example:**
```bash
python crosschannel_tool.py extract cg.pd output_cg
```

This will extract all files from `cg.pd` into the `output_cg` folder, plus a `_pdinfo.json` file that records the archive mode and original file order.

### Repack Files

```bash
python crosschannel_tool.py pack <input_directory> <output.PD> [PackOnly|PackPlus]
```

**Example:**
```bash
python crosschannel_tool.py pack output_cg new_cg.pd
```

This will create a new `new_cg.pd` archive from all files in the `output_cg` folder (subfolders included). If the mode is omitted, it is read from `_pdinfo.json`, falling back to `PackPlus`. Files listed in `_pdinfo.json` keep their original order; new files are appended alphabetically. `_pdinfo.json` itself is never packed.

## Archive Modes

| Mode | Description |
|------|-------------|
| `PackOnly` | No encryption - files are stored as-is |
| `PackPlus` | XOR encryption with `0xFF` - all bytes are inverted |

Most game archives use `PackPlus` mode.

## PD Archive Format

The `.PD` format structure:

| Offset | Size | Description |
|--------|------|-------------|
| 0x00 | 8 bytes | Mode string (`PackOnly` or `PackPlus`) |
| 0x08 | 56 bytes | Padding (zeros) |
| 0x40 | 8 bytes | File count (uint64, little-endian) |
| 0x48 | 144 × N | File directory entries |
| ... | variable | File data |

**Directory Entry (144 bytes per file):**

| Offset | Size | Description |
|--------|------|-------------|
| 0x00 | 128 bytes | Filename (null-terminated, Latin-1) |
| 0x80 | 8 bytes | File offset (uint64, little-endian) |
| 0x88 | 8 bytes | File size (uint64, little-endian) |

## Known Archive Files

| File | Contents |
|------|----------|
| `cg.pd` | CG images, backgrounds, sprites (PNG) |
| `gx.pd` | Additional graphics (PNG) |
| `bgm.pd` | Background music (OGG) |
| `se.pd` | Sound effects (OGG) |
| `voice.pd` | Character voices (OGG) |
| `script.pd` | Game scripts - Japanese (DSF/DEF) |
| `engscr.pd` | Game scripts - English (DSF/DEF) |
| `tofscr.pd` | Additional scripts (DSF/DEF) |

## Examples

**Extract all game assets:**
```bash
python crosschannel_tool.py extract cg.pd extracted/cg
python crosschannel_tool.py extract bgm.pd extracted/bgm
python crosschannel_tool.py extract voice.pd extracted/voice
python crosschannel_tool.py extract script.pd extracted/script
```

**Modify and repack CG:**
```bash
# 1. Extract
python crosschannel_tool.py extract cg.pd mod_cg

# 2. Edit PNG files in mod_cg folder...

# 3. Repack
python crosschannel_tool.py pack mod_cg cg.pd PackPlus
```

## License

This tool is provided for educational and personal use. Please respect the original game developers and publishers.

## Credits

- Developed for extracting assets from CROSS†CHANNEL (FS Engine)
- Original game by FlyingShine / Amaterasu Translations
