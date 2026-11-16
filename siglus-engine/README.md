# Siglus Tools

Tools for translating games on **SiglusEngine** (Visual Art's). They have been tested with **Hoshi Ori Yume Mirai Perfect Edition** (星織ユメミライ Perfect Edition, Tone Work's) with the English fan patch by hanabiworks. With them you can:
- browse the game's resources and preview its images;
- extract everything (images, music, sound effects, voices, video, tables and configuration);
- extract and rebuild the text of `Scene.pck`, with the voice file of each line;
- turn edited images and configuration back into the game's formats.

## Features

- **Graphical interface** (CustomTkinter) and command line
- **Extract everything** in one step: the 5,181 files of Hoshi Ori (11.7 GB) in about 2 minutes with 8 processes
  - G00 images become PNG with transparency. Images made of separate regions (icon strips, letter atlases) are saved as one image; regions that overlap (button states, faces) one image each
  - **CGs and sprites without a face** (`CG_XX_BASE___000`, `BS_AZ11_01B`) are also saved complete, with the first expression of their `_FACE` file on top
  - NWA music and sound effects become `.wav`; each voice of the `.ovk` archives becomes an `.ogg`; OMV videos become `.ogv` (Ogg Theora)
  - `Gameexe.dat` becomes readable text and the `.dbs` tables CSV
  - Extraction runs in parallel, at low priority, and can be cancelled and resumed
- **Scene.pck**:
  - The game key is found automatically, even for games that are not in the tool's list
  - The scenes are disassembled, so text lines, speaker names and other strings (choices, phone messages, titles) are told apart from file and label names, whatever the language
  - Extract the text with the voice file of each line, and optionally the same scenes in another language (for example the Japanese `Scene.pck`) as a reference on each line
  - Rebuild it with your translation. **Translated lines can be any length**: the scenes refer to their strings by number, so only the string table changes and the bytecode stays the same
- **Pack**: edited PNGs go back to `.g00` (keeping the regions and parts of the original) and an edited `Gameexe.txt` back to `Gameexe.dat`, in a folder with the game's structure that you copy over the game

## Files

| File | Description |
|------|-------------|
| `siglus_crypto.py` | The engine's XOR tables, LZ compression and the key search |
| `siglus_pck.py` | `Scene.pck`: reading and writing |
| `siglus_ss.py` | Compiled scenes (`.ss`): strings, bytecode disassembly, text lines and voices |
| `siglus_script.py` | Text extraction and rebuilding |
| `siglus_g00.py` | G00 images (types 0 to 3): decoding and writing |
| `siglus_audio.py` | NWA audio, OVK voice archives and OMV video |
| `siglus_gameexe.py` | `Gameexe.dat` and `.dbs` tables |
| `siglus_extract.py` | Full extraction and packing (shared by the CLI and the GUI) |
| `_siglus.c` | Native LZ and NWA decoding, built into `_siglus.dll` |
| `siglus_common.py` | Shared helpers |
| `siglus_tool.py` | Command line tool (opens the GUI when run without arguments) |
| `siglus_gui.py` | Graphical interface |
| `build_exe.bat` | Builds `_siglus.dll` and a standalone `.exe` with PyInstaller |

## Requirements

- Python 3.8+
- Pillow, NumPy
- CustomTkinter (GUI only)
- gcc, optional, to build `_siglus.dll`

```bash
pip install -r requirements.txt
gcc -O2 -shared -static-libgcc -o _siglus.dll _siglus.c
```

Without `_siglus.dll` the tools use a pure Python version of the same code, which is much slower: a 2-minute music track takes about 7 seconds instead of 0.05. `build_exe.bat` builds the DLL automatically when gcc is installed (for example from MSYS2) and puts it inside the `.exe`.

## Graphical Interface

```bash
python siglus_gui.py
```

- **Game Files**: open the game folder and filter its files by name or type.
  - **Preview**: selecting an image shows it over a checkerboard (to see transparency), with its type and the name of the PNG it is extracted as; ◀ ▶ (or the arrow keys) go through the regions of an image that has several. A CG or sprite without a face is shown with each of its expressions. Audio shows its length, `.ovk` archives their number of voices, tables and `Gameexe.dat` their first lines.
  - **Extract selected** / **Extract all**: save the selected files or the whole game into the output folder, with the folders of the game. **Compose CGs** also saves the CGs and sprites without a face complete, in `g00\_composed`. With **Resume**, an interrupted extraction continues where it stopped; files are processed in parallel by the chosen number of workers.
  - **Pack**: writes the images edited since the extraction as `.g00` and `Gameexe.txt` as `Gameexe.dat` to the output folder (`<game>_new` by default). Copy its contents over the game folder.
- **Scripts**: extract the text of `Scene.pck` with an optional reference (another `Scene.pck`), then rebuild it with your translation. A report lists every changed line.

The default output folders are next to the game folder, never inside it: the tools only read the game files.

### Standalone executable

```bash
pip install -r requirements.txt
build_exe.bat
```

The result is `dist\Siglus_Tools_GUI.exe`, which runs without Python installed.

## Usage

### List files and find the key

```bash
python siglus_tool.py list <game_folder>
python siglus_tool.py findkey <game_folder|Scene.pck>
```

### Extract

```bash
python siglus_tool.py extract <game_folder> <output_folder> [--resume] [--workers N] [--no-composed]
```

**Example:**
```bash
python siglus_tool.py extract "D:\Games\Hoshi Ori" out --resume
```

| Game file | Output |
|-----------|--------|
| `g00\NAME.g00` | `g00\NAME.png`, or `g00\NAME_00.png`, `NAME_01.png`… when its regions overlap |
| `g00\CG_XX_BASE___NNN.g00` with `CG_XX_FACE.g00` | also `g00\_composed\CG_XX_BASE___NNN.png`: the CG with its first face |
| `bgm\NAME.nwa`, `wav\NAME.nwa` | `.wav` |
| `koe\zNNNN.ovk` | `koe\zNNNN\zNNNN_IIIII.ogg`, one per voice |
| `mov\NAME.omv` | `mov\NAME.ogv` (`.wmv` files are copied) |
| `dat\NAME.dbs` | `dat\NAME.dbs` and `dat\NAME.csv` |
| `Gameexe.dat` | `Gameexe.txt` |
| Fonts, `.gan` animations, loose `.ogg` | Unchanged |

**Options:**
- `--resume`: skip the files already done. Finished names are listed in `<output>\_progress.txt`
- `--workers`: number of parallel processes. The default is half of the CPU cores
- `--no-composed`: do not save the CGs and sprites without a face complete

Many CGs are stored without a face: `CG_XX_BASE___000`, `001`… are the variants of the picture, and the expressions are the regions of `CG_XX_FACE.g00`, each as big as the CG and transparent except for the face (`CG_XX_FACE2.g00` holds other mouths). The game picks the face while it runs; the composed PNG uses the first one, and the preview shows them all. Character sprites work the same way (`BS_AZ11_01B` with `BS_AZ11_face`). Composed images are only for viewing: to change a CG, edit its base or its face file.

Extraction also writes `_files.json` with a hash of every file written, so that packing knows which ones you edited.

### Pack

```bash
python siglus_tool.py pack <extracted_folder> <game_folder> <output_folder>
```

**Example:**
```bash
python siglus_tool.py pack out "D:\Games\Hoshi Ori" "D:\Games\Hoshi Ori_new"
```

Edited images must keep their size. A G00 with regions keeps the regions and the parts of the original and only its pixels change; pixels you draw outside those parts are lost, and the tool says how many. Images are stored uncompressed, so the new `.g00` files are larger than the originals. Copy the output folder over the game folder (keep a copy of the originals).

### Text

```bash
python siglus_tool.py text <game_folder|Scene.pck> <text_folder> [--reference <Scene.pck>] [--json]
python siglus_tool.py text-rebuild <game_folder|Scene.pck> <text_folder> <output Scene.pck>
```

**Example:**
```bash
python siglus_tool.py text "D:\Games\Hoshi Ori" text
python siglus_tool.py text-rebuild "D:\Games\Hoshi Ori" text "D:\Games\Hoshi Ori_new\Scene.pck"
```

`text` writes one file per scene. Each line is `NUMBER|TEXT`, where the number is the string's number in the scene; translate only the text after `|`:

```
# name
00002|Natsuki
# voice: z9303_00012
00003|「What, you don't want to?」
# name
00004|％Ｂ
00005|「Of course I do... It's just I'm kinda surprised...」
# other
00120|Take the job
```

Lines starting with `#` are comments: `# name` marks a speaker name, `# other` another string (a choice, a phone message, a title), `# voice:` the voice file of the line and `#>` the reference. Keep the engine codes: `％Ｂ` is the protagonist's name, `#N` a line break and `【name】` the sender of a phone message. A sentence can be split over several strings when it contains a variable.

- `--reference`: the same scenes in another language (for example the Japanese `Scene.pck` of the original game, whose key is found automatically). Its text is added to each line as a `#>` comment when it differs
- `--json`: also writes `json\<scene>.json` with the type, voice and reference of each line

`text-rebuild` writes a new `Scene.pck` with the changed scenes; the rest is copied byte for byte. If nothing changed, no file is written. Replace the game's `Scene.pck` with it (keep a copy of the original).

## Supported game

Hoshi Ori Yume Mirai Perfect Edition with the English patch:

| Folder | Contents | Files |
|--------|----------|-------|
| `g00\` | Backgrounds, CGs, sprites, interface | 3,896 G00 (1,727 type 0, 13 type 1, 2,156 type 2); 822 CG bases and 28 sprites have their face apart |
| `bgm\` | Music | 55 NWA |
| `wav\` | Sound effects and background voices | 582 NWA, 3 Ogg |
| `koe\` | Voices | 594 OVK with 37,882 voices |
| `mov\` | Opening, endings and effects | 40 OMV, 2 WMV |
| `dat\` | Music list, calendar, name changes, fonts | 3 DBS, 2 TTF, `tcdata.tcr` |
| `gan\` | Animations | 2 GAN |
| Root | Scenes and configuration | `Scene.pck` (510 scenes), `Gameexe.dat` |

The text has 330,896 strings: 127,420 text lines, 60,611 speaker names and 2,771 other strings; 34,794 lines are linked to their voice.

## Format Reference

### Encryption and compression

The engine has two fixed 256-byte tables, applied with XOR by position: `TABLE_A` for the scenes of `Scene.pck` and `TABLE_B` for `Gameexe.dat`. Each game also has a 16-byte key for the scenes. The LZ compression is:

```
u32 packed_size (with this 8-byte header) · u32 size
flags byte (least significant bit first): 1 = literal byte; 0 = u16 v: copy (v & 0xF) + 2 bytes from v >> 4 back
```

G00 images use the same LZ with 3-byte units (type 0) or a minimum copy of 1 unit.

**Finding the key**: once `TABLE_A` is removed, every scene starts with its own packed size, which gives bytes 0–3 of the key. The text is UTF-16, so the compressed data has many zero bytes: at each position modulo 16, the most frequent byte is the key byte. The key is accepted only when every scene then decompresses to a valid scene. The key of Hoshi Ori PE with the English patch is `EA F6 59 37 AD 33 8B CD 67 32 71 E3 20 47 B3 38`.

### Scene.pck

```
u32 header_size
10 × (u32 offset, u32 count): var_info, var_name_index, var_name, cmd_info, cmd_name_index,
    cmd_name, scn_name_index, scn_name, scn_info, scn_data
u32 extra_key_use · u32 source_header_size
```

`scn_name_index` gives (offset, length) in UTF-16 characters inside `scn_name`, and `scn_info` gives (offset, size) of each scene inside `scn_data`. Each scene is XOR key, XOR `TABLE_A` and LZ.

### Scenes (.ss)

```
u32 0x84 · 16 × (u32 offset, u32 count): bytecode, string index, string data, labels, z-labels,
    command labels, properties, commands..., read flags
string index: (u32 offset, u32 length) in characters; each UTF-16 unit XOR (28807 × string number)
```

The bytecode is a stack machine. The opcodes that matter for translation are `CD_PUSH` (0x02: form, value; form 20 is a string number, form 10 an integer), `CD_TEXT` (0x31: shows the string just pushed), `CD_NAME` (0x32: sets the speaker) and `CD_COMMAND` (0x30: calls a command with the pushed arguments; some commands carry a trailing read-flag number). A voice is an integer `scene × 100000 + n` pushed before the line, which plays entry `n` of `koe\z<scene>.ovk`.

### G00

```
u8 type · u16 width · u16 height
type 0: LZ (3-byte units) of 24-bit BGR pixels
type 1: LZ: u16 colors · BGRA palette · 8-bit indexes
type 2: u32 regions · regions × (x1, y1, x2, y2, ox, oy) i32 · LZ with u32 regions · (offset, size)
        per region · per region: u16 1 · u16 parts · 0x70 bytes · parts: u16 x · u16 y · i16 ·
        u16 width · u16 height · 0x52 bytes · BGRA pixels
type 3: JPEG XOR a fixed 256-byte key
```

### NWA, OVK and OMV

- **NWA**: a 0x2C-byte header (channels, bits, rate, compression level, RLE, blocks, sizes) and a table of block offsets. Each block has the first sample of each channel and then differences in a bit stream whose widths depend on the level. Level −1 is plain PCM.
- **OVK**: `u32 n · n × (u32 size, u32 offset, u32 id, u32 samples)`; each entry is a complete Ogg Vorbis file.
- **OMV**: an index of frames, then an Ogg Theora stream from the first `OggS`.

### Gameexe.dat and .dbs

- **Gameexe.dat**: `u32 0 · u32 uses_key ·` data XOR `TABLE_B` (and the game key if `uses_key`), LZ, UTF-16 text.
- **.dbs**: `u32 encoding (0 = Shift-JIS)` · data XOR `2D 62 F4 89`, LZ, then XOR with two 4-byte keys in a fixed order. The table has a header with the number of rows and columns, the column types (`0x53` text, `0x56` integer), the values and the text.

## Credits

- [GARbro](https://github.com/morkt/GARbro) (MIT): reference for G00, NWA and OVK; the G00 and NWA decoders follow its code
- [siglus_rs](https://github.com/xmoezzz/siglus_rs) (MPL-2.0): reference for the scene bytecode and its optional command read flags
- [hiroshil/SiglusEngine](https://github.com/hiroshil/SiglusEngine): reference for `Scene.pck`, `Gameexe.dat` and `.dbs` (formats reimplemented, no code copied)
