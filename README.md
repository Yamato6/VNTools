# VNTools

Tools for extracting, editing and repacking the assets of Japanese visual novels, mainly for **fan translation**. Each tool comes with a graphical interface and a command line, and can be built into a standalone Windows `.exe`.

| Tool | Engine | Games | Formats |
|------|--------|-------|---------|
| [**CrossChannel MultiTool**](fs-engine/) | FS Engine | CROSS†CHANNEL | `.pd` archives |
| [**FVP Tools**](fvp/) | Favorite View Point System | Hoshizora no Memoria, Irotoridori no Sekai, Astral Air… | `.bin` archives, `.nvsg` images, `.hcb` scripts |

## Features

**CrossChannel MultiTool** (`fs-engine/`)
- Browse a `.pd` archive and extract everything or only the files you select
- Repack a folder into a `.pd`; the original mode (`PackOnly` / `PackPlus`) and file order are detected automatically

**FVP Tools** (`fvp/`)
- Browse, extract and repack `.bin` archives, with image preview
- Convert NVSG images to PNG and back, in batch
- Extract the script text from `.hcb` files and rebuild them with your translation
- **Translated lines can be longer than the original.** The rebuild relocates every jump, call and function pointer in the script, so a line can grow up to the format limit of 254 bytes
- Character table for custom fonts (write `canción`, the game gets the characters your font draws as `ó`)
- Rebuild report listing lines that grew, were cut or had characters replaced

## Quick start

Requires Python 3.8 or newer.

```bash
git clone https://github.com/Yamato6/VNTools.git
cd VNTools/fvp                 # or fs-engine
pip install -r requirements.txt
python fvp_gui.py              # or: python crosschannel_gui.py
```

Running the command line tool without arguments also opens the GUI:

```bash
python fvp_tools.py                                    # GUI
python fvp_tools.py hcb-strings script.hcb strings.txt # command line
```

See each tool's README for every command and the file format documentation:
- [fs-engine/README.md](fs-engine/README.md)
- [fvp/README.md](fvp/README.md)

## Standalone executable

Each tool folder has a `build_exe.bat` that builds a single-file `.exe` with PyInstaller (installed by `requirements.txt`). The result goes to `dist/` inside that folder and runs without Python.

## Repository layout

```
VNTools/
├── fs-engine/            CrossChannel MultiTool
│   ├── pd_core.py          .pd format logic
│   ├── crosschannel_tool.py  command line
│   ├── crosschannel_gui.py   graphical interface
│   └── build_exe.bat
└── fvp/                  FVP Tools
    ├── fvp_bin.py          .bin archives
    ├── fvp_nvsg.py         NVSG images
    ├── fvp_hcb.py          HCB scripts
    ├── fvp_common.py       shared helpers
    ├── fvp_tools.py        command line
    ├── fvp_gui.py          graphical interface
    ├── charmap_es.txt      character table template
    └── build_exe.bat
```

## Credits

- HCB format research: binaryfail, akerou (original C++ tool)

## Disclaimer

These tools are provided for personal and educational use. They do not include any game data: you need your own copy of the games. Please respect the copyrights of the original developers and publishers.
