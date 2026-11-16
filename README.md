# VNTools

Tools for extracting, editing and repacking the assets of Japanese visual novels, mainly for **fan translation**. Each tool comes with a graphical interface and a command line, and can be built into a standalone Windows `.exe`.

| Tool | Engine | Games | Formats |
|------|--------|-------|---------|
| [**CrossChannel MultiTool**](fs-engine/) | FS Engine | CROSS†CHANNEL | `.pd` archives |
| [**FVP Tools**](fvp/) | Favorite View Point System | Hoshizora no Memoria, Irotoridori no Sekai, Astral Air… | `.bin` archives, `.nvsg` images, `.hcb` scripts |
| [**Yeti Tools**](yeti-engine/) | Yeti/Regista | CROSS†CHANNEL -Final Complete- (PC) | `.cpk` archives, `.xtx` textures, `sn.bin` scripts, audio |
| [**AdvHD Tools**](advhd-engine/) | AdvHD (WillPlus) | CROSS†CHANNEL (WillPlus edition, 2012) | `.arc` archives, WIPF images, `.wsc` scripts, audio |
| [**NeXAS Tools**](nexas-engine/) | NeXAS (Giga) | Maji de Watashi ni Koi Shinasai! S | `.pac` archives, `.bin` scripts, `.dat` tables, images, audio, video |
| [**CatSystem2 Tools**](cs2-engine/) | CatSystem2 | Grisaia no Kajitsu, Grisaia no Meikyuu, Grisaia no Rakuen | encrypted `.int` archives, HG-3 images, `.cst` scripts, audio, video |
| [**BGI Tools**](bgi-engine/) | BGI / Ethornell | Daitoshokan no Hitsujikai, Koi to Senkyo to Chocolate HR | `.arc` archives, CompressedBG images, scripts, audio, video |
| [**KiriKiri Tools**](krkr-engine/) | KiriKiri 2 (KAG / KAGEX) | Noble Works | `.xp3` archives, TLG images, layered sprites, scene databases, PSB |
| [**Siglus Tools**](siglus-engine/) | SiglusEngine | Hoshi Ori Yume Mirai Perfect Edition | `Scene.pck` scripts, G00 images, NWA/OVK audio, OMV video, `Gameexe.dat` |

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
- Generate that custom font from any TrueType font and the same character table (`font-patch`)
- Rebuild report listing lines that grew, were cut or had characters replaced

**Yeti Tools** (`yeti-engine/`)
- Browse a `.cpk` archive (CRIWARE) and extract its files, already decompressed (CRILAYLA or Yeti LZSS), and put edited files back into a `.cpk`
- Extract every image of the game as PNG in one step, untiling the Xbox 360 textures (ARGB8888, ARGB4444, DXT5), and **repack the edited images** in their original format
- Extract the script text of `sn.bin` and rebuild it with your translation. **Translated lines can be longer than the original**: every jump, switch and choice target is relocated
- Extract the music, sound effects and voices (HCA, and WAV with vgmstream)
- Parallel extraction at low priority that can be cancelled and resumed
- Convert loose `.xtx` textures such as the game font, optionally one image per channel

**AdvHD Tools** (`advhd-engine/`)
- Browse a `.arc` archive, extract everything or only the files you select, and pack a folder back into a `.arc`
- Extract every image of the game as PNG with transparency, combining each image with its opacity mask, and **repack the edited images** (only the changed ones are re-encoded)
- Extract the script text and rebuild it with your translation. **Translated lines can be longer than the original**: every jump and conditional block is relocated
- Extract the music, sound effects and voices as `.ogg`
- Parallel extraction at low priority that can be cancelled and resumed

**NeXAS Tools** (`nexas-engine/`)
- Browse a `.pac` archive, extract everything in the game in one step (images, audio, video, scripts, data tables) and resume an interrupted extraction
- Extract the script text and rebuild it with your translation. **Translated lines can be any length**: the scripts refer to their strings by number. Each line can carry the English patch's text as a reference
- Extract and rebuild the interface texts of the `.dat` tables
- **Repack the edited files** (only the changed ones are re-encoded), into a full `.pac` or a small `UpdateN.pac` patch that the game loads on top of its own files
- Convert FIL masks to PNG and back, NMV animations to PNG frames, and compose the character sprites (SPM)

**CatSystem2 Tools** (`cs2-engine/`)
- Open the encrypted `.int` archives: the game passphrase is read from the game's `.exe`
- Extract everything in the game in one step and resume an interrupted extraction. HG-3 images become PNG with transparency, one PNG per frame
- Extract the script text and rebuild it with your translation. **Translated lines can be any length**: the scripts refer to their lines by number. Each spoken line comes with its voice file
- **Repack the edited files**: only the changed ones are re-encoded, and the archive keeps the original encryption. Repacking an unchanged folder gives an archive identical to the original

**BGI Tools** (`bgi-engine/`)
- Browse a `.arc` archive with image preview, and extract everything in the game in one step, resuming an interrupted extraction. CompressedBG images become PNG with transparency and the audio `.ogg`
- Loose files in the game folder (such as those of a fan patch) are extracted separately
- Extract the script text and rebuild it with your translation. **Translated lines can be any length**. Each line can carry the text of another language (for example the English patch) as a reference
- **Repack the edited files**: only the changed ones are re-encoded; edited PNGs and `.ogg` files go back into the archive

**KiriKiri Tools** (`krkr-engine/`)
- Browse a `.xp3` archive with image preview, and extract everything in the game in one step, resuming an interrupted extraction. TLG images become PNG and PSB files readable JSON
- Compose the layered character sprites (each outfit with each face), in the preview and as PNG
- Extract the text of the scene databases (lines, names, choices, scene titles) with the voice file of each line, and the text under a translation patch as a reference. **Translated lines can be any length**
- **Make a patch** (`patch4.xp3`) with the edited files and the translated text, which the game loads on top of its own files: the original archives are never touched

**Siglus Tools** (`siglus-engine/`)
- Browse the game's resources with image preview, and extract everything in one step, resuming an interrupted extraction. G00 images become PNG, NWA audio `.wav`, the voices of the `.ovk` archives `.ogg` and OMV videos `.ogv`
- The `Scene.pck` key is found automatically, and the scenes are disassembled to tell text, speaker names and voices apart from file names
- Extract the text with the voice file of each line and another language as a reference, and rebuild it. **Translated lines can be any length**
- **Pack** edited images back to `.g00` and `Gameexe.txt` back to `Gameexe.dat`

## Quick start

Requires Python 3.8 or newer (3.11 or newer for KiriKiri Tools).

```bash
git clone https://github.com/Yamato6/VNTools.git
cd VNTools/fvp                 # or fs-engine, yeti-engine, advhd-engine, nexas-engine, cs2-engine, bgi-engine, krkr-engine, siglus-engine
pip install -r requirements.txt
python fvp_gui.py              # or: python crosschannel_gui.py, yeti_gui.py, advhd_gui.py, nexas_gui.py, cs2_gui.py, bgi_gui.py, krkr_gui.py, siglus_gui.py
```

Running the command line tool without arguments also opens the GUI:

```bash
python fvp_tools.py                                    # GUI
python fvp_tools.py hcb-strings script.hcb strings.txt # command line
```

See each tool's README for every command and the file format documentation:
- [fs-engine/README.md](fs-engine/README.md)
- [fvp/README.md](fvp/README.md)
- [yeti-engine/README.md](yeti-engine/README.md)
- [advhd-engine/README.md](advhd-engine/README.md)
- [nexas-engine/README.md](nexas-engine/README.md)
- [cs2-engine/README.md](cs2-engine/README.md)
- [bgi-engine/README.md](bgi-engine/README.md)
- [krkr-engine/README.md](krkr-engine/README.md)
- [siglus-engine/README.md](siglus-engine/README.md)

## Standalone executable

Each tool folder has a `build_exe.bat` that builds a single-file `.exe` with PyInstaller (installed by `requirements.txt`). The result goes to `dist/` inside that folder and runs without Python.

## Repository layout

```
VNTools/
├── advhd-engine/         AdvHD Tools
│   ├── advhd_arc.py        .arc archives
│   ├── advhd_wipf.py       WIPF images and LZSS
│   ├── advhd_wsc.py        WSC scripts
│   ├── advhd_extract.py    image and audio extraction, image repacking
│   ├── advhd_common.py     shared helpers
│   ├── advhd_tool.py       command line
│   ├── advhd_gui.py        graphical interface
│   └── build_exe.bat
├── bgi-engine/           BGI Tools
│   ├── bgi_arc.py          .arc archives
│   ├── bgi_codecs.py       key generator, DSC compression and BSE encryption
│   ├── bgi_image.py        CompressedBG and BGI images
│   ├── bgi_audio.py        bw audio
│   ├── bgi_script.py       compiled scripts
│   ├── bgi_extract.py      full extraction and repacking
│   ├── _bgi.c              native CompressedBG decoding
│   ├── bgi_common.py       shared helpers
│   ├── bgi_tool.py         command line
│   ├── bgi_gui.py          graphical interface
│   └── build_exe.bat
├── cs2-engine/           CatSystem2 Tools
│   ├── cs2_int.py          .int archives
│   ├── cs2_crypto.py       passphrase, Blowfish and Mersenne Twister
│   ├── cs2_hg3.py          HG-3 images
│   ├── cs2_script.py       CST scripts
│   ├── cs2_extract.py      full extraction and repacking
│   ├── _cs2.c              native Blowfish and HG-3 code
│   ├── cs2_common.py       shared helpers
│   ├── cs2_tool.py         command line
│   ├── cs2_gui.py          graphical interface
│   └── build_exe.bat
├── fs-engine/            CrossChannel MultiTool
│   ├── pd_core.py          .pd format logic
│   ├── crosschannel_tool.py  command line
│   ├── crosschannel_gui.py   graphical interface
│   └── build_exe.bat
├── fvp/                  FVP Tools
│   ├── fvp_bin.py          .bin archives
│   ├── fvp_nvsg.py         NVSG images
│   ├── fvp_hcb.py          HCB scripts
│   ├── fvp_font.py         custom font generation
│   ├── fvp_common.py       shared helpers
│   ├── fvp_tools.py        command line
│   ├── fvp_gui.py          graphical interface
│   ├── charmap_es.txt      character table template
│   └── build_exe.bat
├── krkr-engine/          KiriKiri Tools
│   ├── krkr_xp3.py         .xp3 archives and patches
│   ├── krkr_tlg.py         TLG5 and TLG6 images
│   ├── krkr_image.py       file types and layered sprites
│   ├── krkr_psb.py         PSB to JSON
│   ├── krkr_script.py      scene database text
│   ├── krkr_extract.py     full extraction and patch packing
│   ├── _krkr.c             native TLG decoding
│   ├── krkr_common.py      shared helpers
│   ├── krkr_tool.py        command line
│   ├── krkr_gui.py         graphical interface
│   └── build_exe.bat
├── nexas-engine/         NeXAS Tools
│   ├── nexas_pac.py        .pac archives
│   ├── nexas_compress.py   Huffman and LZSS
│   ├── nexas_script.py     script and table text
│   ├── nexas_formats.py    FIL, NMV, SPM and DAT formats
│   ├── nexas_extract.py    full extraction and repacking
│   ├── nexas_common.py     shared helpers
│   ├── nexas_tool.py       command line
│   ├── nexas_gui.py        graphical interface
│   └── build_exe.bat
├── siglus-engine/        Siglus Tools
│   ├── siglus_crypto.py    XOR tables, LZ and key search
│   ├── siglus_pck.py       Scene.pck
│   ├── siglus_ss.py        compiled scenes and bytecode
│   ├── siglus_script.py    text extraction and rebuilding
│   ├── siglus_g00.py       G00 images
│   ├── siglus_audio.py     NWA, OVK and OMV
│   ├── siglus_gameexe.py   Gameexe.dat and .dbs tables
│   ├── siglus_extract.py   full extraction and packing
│   ├── _siglus.c           native LZ and NWA decoding
│   ├── siglus_common.py    shared helpers
│   ├── siglus_tool.py      command line
│   ├── siglus_gui.py       graphical interface
│   └── build_exe.bat
└── yeti-engine/          Yeti Tools
    ├── yeti_cpk.py         .cpk archives
    ├── yeti_utf.py         CRIWARE @UTF tables
    ├── yeti_codecs.py      CRILAYLA and Yeti LZSS
    ├── _crilayla.c         native CRILAYLA decompressor
    ├── yeti_xtx.py         pak containers and XTX textures
    ├── yeti_script.py      sn.bin scripts
    ├── yeti_extract.py     image and audio extraction, repacking
    ├── yeti_common.py      shared helpers
    ├── yeti_tool.py        command line
    ├── yeti_gui.py         graphical interface
    └── build_exe.bat
```

## Credits

- HCB format research: binaryfail, akerou (original C++ tool)
- WillPlus format reference: [vn-tools/arc_unpacker](https://github.com/vn-tools/arc_unpacker)
- CPK and Yeti format references: see [yeti-engine/README.md](yeti-engine/README.md#credits)
- NeXAS PAC, CatSystem2 INT, HG-3, BGI, XP3, TLG, G00 and NWA format reference: [morkt/GARbro](https://github.com/morkt/GARbro)
- PSB format reference: [UlyssesWu/FreeMote](https://github.com/UlyssesWu/FreeMote)
- SiglusEngine references: [xmoezzz/siglus_rs](https://github.com/xmoezzz/siglus_rs) (scene bytecode), [hiroshil/SiglusEngine](https://github.com/hiroshil/SiglusEngine)

## License

The code in this repository is released under the [MIT License](LICENSE).

## Disclaimer

These tools do not include any game data, fonts or copyrighted assets: you need your own legally obtained copy of the games. Game names and trademarks belong to their respective owners, and this project is not affiliated with or endorsed by them. Please respect the copyrights of the original developers and publishers, and only distribute translation patches, never the game files themselves.
