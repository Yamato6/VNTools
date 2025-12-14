# CrossChannel MultiTool

A Python tool for extracting and repacking `.PD` archive files used by the **FS Engine** in the visual novel **CROSS†CHANNEL**.

## Features

- **Extract** all files from `.PD` archives
- **Repack** files into new `.PD` archives
- Supports both `PackOnly` and `PackPlus` formats
- Automatic XOR decryption/encryption for `PackPlus` archives
- Preserves original file structure

## Requirements

- Python 3.6 or higher
- No external dependencies required

## Installation

Simply download `crosschannel_tool.py` to your desired directory.

## Usage

### Extract Files

```bash
python crosschannel_tool.py extract <archive.PD> <output_directory>
```

**Example:**
```bash
python crosschannel_tool.py extract cg.pd output_cg
```

This will extract all files from `cg.pd` into the `output_cg` folder.

### Repack Files

```bash
python crosschannel_tool.py pack <input_directory> <output.PD> <PackOnly|PackPlus>
```

**Example:**
```bash
python crosschannel_tool.py pack output_cg new_cg.pd PackPlus
```

This will create a new `new_cg.pd` archive from all files in the `output_cg` folder.

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
