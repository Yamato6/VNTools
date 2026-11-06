# CatSystem2 Tools

Tools for translating games on the **CatSystem2** engine. They have been tested with the Grisaia trilogy by Frontwing: **Grisaia no Kajitsu**, **Grisaia no Meikyuu** and **Grisaia no Rakuen** (2024 Full HD editions). With them you can:
- open the encrypted `.int` archives;
- extract everything inside them (images, music, voices, video and scripts);
- extract and rebuild the script text;
- pack edited files back into archives that the game reads.

## Features

- **Graphical interface** (CustomTkinter) and command line
- **Encrypted archives**: the game passphrase is read from the game's `.exe`, or from `config\startup.xml` if there is no `.exe`. You can also type it yourself
- **Extract everything** in one step: about 109,000 files from the 28 archives of Grisaia no Kajitsu (11 GB)
  - HG-3 images are converted to PNG with transparency, one PNG per frame
  - Music, voices, sound effects, video and every other file are saved unchanged
  - Extraction runs in parallel, at low priority, and can be cancelled and resumed
- **Repack** the files you edited:
  - Only the changed files are re-encoded; edited PNGs are converted back to HG-3
  - Everything else is copied byte for byte, without decrypting it
  - Repacking an unchanged folder gives an archive identical to the original
- **Scripts**:
  - Extract the text, character names, scene titles and choices of every scene (`.cst`)
  - The voice file of each line is included as a comment
  - Rebuild the scripts with your translation. **Translated lines can be any length**: the scripts refer to their lines by number, so nothing else has to be relocated
- Supports a character table (`--charmap`, same format as FVP Tools and NeXAS Tools) for letters that Shift-JIS lacks, such as `ñ` or `á`

## Files

| File | Description |
|------|-------------|
| `cs2_int.py` | `.int` (KIF) archives: reading, writing and repacking, with or without encryption |
| `cs2_crypto.py` | Game passphrase and key, Blowfish and Mersenne Twister |
| `cs2_hg3.py` | HG-3 images: decoding every frame and encoding edited frames |
| `cs2_script.py` | CatScene (`.cst`) scripts: text extraction and rebuilding |
| `cs2_extract.py` | Full extraction and repacking (shared by the CLI and the GUI) |
| `_cs2.c` | Native Blowfish and HG-3 code, built into `_cs2.dll` |
| `cs2_common.py` | Shared helpers and the character table |
| `cs2_tool.py` | Command line tool (opens the GUI when run without arguments) |
| `cs2_gui.py` | Graphical interface |
| `build_exe.bat` | Builds `_cs2.dll` and a standalone `.exe` with PyInstaller |

## Requirements

- Python 3.8+
- Pillow
- CustomTkinter (GUI only)
- gcc, optional, to build `_cs2.dll`

```bash
pip install -r requirements.txt
gcc -O2 -shared -static-libgcc -o _cs2.dll _cs2.c
```

Without `_cs2.dll` the tools use a pure Python version of the same code. Images are still decoded reasonably fast, but archives are decrypted at about 1 MB/s instead of about 180 MB/s, so extracting a whole game takes hours instead of minutes. `build_exe.bat` builds the DLL automatically when gcc is installed (for example from MSYS2) and puts it inside the `.exe`.

## Graphical Interface

```bash
python cs2_gui.py
```

The passphrase at the top of the window is filled in when you choose a game folder or a `.int`. The key it gives is shown next to it.

- **INT Files**: open a `.int` and filter its files by name or type.
  - **Preview**: selecting a file shows its images over a checkerboard (to see transparency), with their size and the name of the PNG they are extracted as. ◀ ▶ (or the arrow keys) step through the frames of an HG-3 image (with their position and canvas).
  - **Extract selected** / **Extract all**: save the selected files or the whole archive into `<output>/<archive>/`, with the HG-3 images as PNG and the rest decrypted. **Extract game…** does the same for every archive of a game folder. With **Resume**, an interrupted extraction continues where it stopped; files are processed in parallel by the chosen number of workers.
  - **Pack**: builds a complete `.int`, encrypted like the open one. A folder from the extraction only re-encodes the edited files; any other folder is packed as it is.
- **Scripts**: extract the text of `scene.int`, then rebuild it with your translation. A report lists the lines that could not be encoded.

The default output folders are next to the game folder, never inside it: the tools only read the game files.

### Standalone executable

```bash
pip install -r requirements.txt
build_exe.bat
```

The result is `dist\CS2_Tools_GUI.exe`, which runs without Python installed.

## Usage

Every command accepts `--key <passphrase>`. Without it, the passphrase is read from the game folder of the `.int`.

### Passphrase

```bash
python cs2_tool.py key <game_folder|game.exe>
```

```
Passphrase: FW-XGHSGKCJ  (from Grisaia.exe)
Key:        0xFFE695D1
```

### List files

```bash
python cs2_tool.py list <file.int>
```

### Extract

```bash
python cs2_tool.py extract <game_folder|file.int> <output_folder> [--only images,audio,video,scripts,data]
                           [--resume] [--raw] [--workers N]
```

**Example:**
```bash
python cs2_tool.py extract "D:\Games\Grisaia no Kajitsu" out --resume
```

Each archive goes to `<output>\<archive>\`, with every file under its own name.

| Files | Output |
|-------|--------|
| `.hg3` | `NAME.png`, or `NAME_00.png`, `NAME_01.png`… for images with several frames. Unless `--raw` is given |
| `.ogg`, `.wmv`, `.mpg`, `.cst`, `.anm`, `.kcs`, `.fes`, `.kx2`, `.zt`, `.xml`, `.csv`, `.dat` | Unchanged |

The frames of an image are the parts the game draws on a shared canvas. For example, a character's body comes first, followed by their faces and mouth shapes.

**Options:**
- `--only`: extract only these categories:
  - images: `hg3` and `anm`
  - audio: `ogg`
  - video: `wmv` and `mpg`
  - scripts: `cst`, `kcs` and `fes`
  - data: everything else
- `--resume`: skip the files already done. Finished names are listed in `<output>\<archive>\_progress.txt`
- `--raw`: keep the HG-3 images as `.hg3`
- `--workers`: number of parallel processes. The default is half of the CPU cores

Extraction also writes `_files.json` in each archive folder. It holds a hash of every file, so that repacking knows which ones you edited.

### Repack

```bash
python cs2_tool.py repack <original.int> <folder> <output.int> [--add-new]
```

`folder` is the output of `extract`, or the folder of that archive inside it. How each file is treated:
- **Changed files**: only files whose content changed since the extraction are taken.
- **Edited PNGs**: they are converted back to HG-3. Only the edited frames are re-encoded, and the other sections of the image are kept. Images must keep their original size.
- **Errors**: if any file cannot be encoded, no file is written and the errors are listed.

The output is a complete `.int`, encrypted the same way as the original. Replace the game's `.int` with it, and keep a copy of the original.

- `--add-new`: also add the files of the folder that are not in the original archive

**Example:**
```bash
python cs2_tool.py extract "D:\Games\Grisaia no Kajitsu\image03.int" out
# edit out\image03\*.png…
python cs2_tool.py repack "D:\Games\Grisaia no Kajitsu\image03.int" out image03.int
```

### Pack a folder

```bash
python cs2_tool.py pack <folder> <output.int> [--like <original.int>]
```

Packs every file of a folder and its subfolders. Files starting with `_` are skipped.
- **With `--like`**: files that were in the original archive keep their order, new files go at the end, and the archive is encrypted like the original.
- **Without `--like`**: files are sorted by name and the archive is not encrypted.

### Text

```bash
python cs2_tool.py text <scene.int> <text_folder> [--json] [--charmap <map.txt> --reverse]
python cs2_tool.py text-rebuild <scene.int> <text_folder> <output.int> [--charmap <map.txt>]
```

`text` writes one file per script, `<script>.cst.txt`. Each text line holds the line number in the script and the text:
- Character names, scene titles and choices are marked with a comment.
- Each spoken line is preceded by its voice file.

```
# title
0000|軽い女？　重い女？
0006|２０１１年７月１５日７時３０分・美浜学園・正門前
# voice: AMA_ama_001_001
# name
0072|天音
0073|「あ、ユージお帰り～♪」
# choice
1860|天音と一緒に逃げる
```

Translate only the text after `|`. Keep the control codes as they are:
- `\n`: line break
- `\@`, `\r` and `\w0`
- the square brackets `[…]` around some words

The voice files (`AMA_ama_001_001`) are `.ogg` files in `pcm_*.int`.

`--json` also writes `json\<script>.cst.json` with the type, text and voice file of each line.

`text-rebuild` takes the original `scene.int` and the folder with the translated files, and writes a complete `scene.int`. Scripts without a text file, or without changes, are copied unchanged.

Only **Shift-JIS (CP932)** text is supported. Characters outside it (ñ, á, ¿…) keep the original line and are listed in the report. To use them, map them with a character table (`--charmap`, one `á=ｱ` pair per line, same format as FVP Tools) to characters that a custom font draws as them. Saved games may not work after the scripts change.

**Example:**
```bash
python cs2_tool.py text "D:\Games\Grisaia no Kajitsu\scene.int" text
# translate text\*.txt…
python cs2_tool.py text-rebuild "D:\Games\Grisaia no Kajitsu\scene.int" text scene.int
```

## Game Files

Grisaia no Kajitsu:

| File | Contents | Entries |
|------|----------|---------|
| `scene.int` | Scripts | 278 `.cst` |
| `image00.int` … `image07.int` | Backgrounds, event CG, character sprites, interface | 77,773 HG-3, 205 `.anm` |
| `bgm.int`, `se.int` | Music and sound effects | 41 and 1,172 OGG |
| `pcm_a.int` … `pcm_i.int` | Voices | 29,317 OGG |
| `movie.int` | Opening, endings and commercials | 7 WMV, 6 MPG |
| `config.int` | Settings (XML), character names (`nametable.csv`), CG gallery (`cglist.dat`) | 8 |
| `export.int` | Extras (`.zt`) | 81 |
| `kcs.int`, `mot.int`, `ptcl.int` | Compiled system scripts, motions and particles (`.kcs`) | 18, 55, 13 |
| `fes.int`, `kx2.int` | Menu screens (`.fes`) and effects (`.kx2`) | 40, 3 |

The other two games use the same archives (Rakuen has `pcm_a` to `pcm_l`) and the same passphrase.

## File Formats

All values are little-endian.

### INT archive (KIF)

```
char  magic[4] = "KIF\0"
u32   entry_count
entry_count × { char name[0x40]; u32 offset; u32 size }
...   data
```

**Encryption.** An archive is encrypted when its first entry is `__key__.dat`. The keys come from these values:
- **Passphrase**: stored in the game `.exe`, in the `V_CODE2`/`DATA` resource. It is encrypted with Blowfish; the Blowfish key is the `KEY_CODE`/`KEY` resource with every byte XORed with `0xCD`.
- **Main key**: the CRC-32 of the passphrase (polynomial `0x04C11DB7`, MSB first, the Shift-JIS bytes).
- **Archive key**: the size field of `__key__.dat` is a seed. It goes through a Mersenne Twister seeded the old way, with the Knuth 69069 generator; the first number it gives, as 4 bytes, is the Blowfish key of the archive.

With those keys, every entry `i` (from 1) is decoded as follows:
- `offset` and `size`: deciphered together as one Blowfish block, after adding `i` to the stored offset.
- Name: a Caesar shift over the letters (`zyx…cbaZYX…CBA`). The shift is the sum of the four bytes of the first Mersenne Twister number seeded with `main key + i`, and it grows by one for each character.
- Data: Blowfish ECB, with every 8-byte block read as two little-endian `u32`. The last bytes, when there are fewer than 8, are not encrypted.

In the encrypted archives of these games, the data of each entry is preceded by a copy of the previous table entry (0x48 bytes). The tool writes them the same way.

### HG-3 image

```
char "HG-3" · u32 header_size (12) · u32 version (0x300)
frames:   u32 frame_size (0 = last) · u32 id · sections
section:  char name[8] · u32 section_size (0 = last) · u32 body_size · body
stdinfo:  u32 width, height, bpp, x, y, canvas_width, canvas_height, ...
img0000:  u32 first_row, rows, packed_data, data_size, packed_control, control_size
          data (zlib) · control (zlib)
```

Each frame also has `cptype` and `imgmode` sections, and sprites have `ats0001`…; the tool keeps them unchanged. Images are stored bottom-up in BGRA order.

`img0000` is decoded in these steps:
1. **Control stream**: a bit stream read from the least significant bit. It starts with one bit, which says whether the first run copies data or writes zeros, followed by the total size.
2. **Runs**: Elias-gamma lengths that alternate between bytes copied from the data stream and runs of zeros.
3. **Planes**: the result is split into 4 planes. Each output byte takes 2 bits from each plane.
4. **Byte transform**: every byte goes through `v >> 1`, XORed with `0xFF` when `v` is odd.
5. **Deltas**: a horizontal delta (per pixel) is undone on the first row and a vertical delta on the others.

The encoder does the reverse. On the game's images it gives the same data and control streams as the originals before zlib.

Older games also use `img_jpg`, a JPEG image, with an optional `img_al` section for the alpha. The tool reads these too and writes them back as `img0000`; this path has not been tested on a real game.

### CatScene script (`.cst`)

```
char "CatScene" · u32 packed_size · u32 size · zlib(script)
script:
    u32 size · u32 block_count · u32 line_table · u32 strings      offsets from byte 16
    block_count × { u32 line_count; u32 first_line }               entry points
    one u32 per line: offset of the line from `strings`
    line: u8 0x01 · u8 type · Shift-JIS text + \0
```

| Type | Contents |
|------|----------|
| `0x20` | Text |
| `0x21` | Name of the character who speaks |
| `0x02` | Wait for a click |
| `0x03` | New page |
| `0x30` | Command: `bg 0 bg03 0 0 0`, `pcm AMA_ama_001_001` (voice), `scene <title>`, `str 155 <title>`, `fselect` followed by the choices `0 ama06a <text>`… |

Since the entry points refer to line numbers, rebuilding only regenerates the strings and the line table.

## License

Released under the [MIT License](../LICENSE). No game data is included; you need your own copy of the game. Please respect the original game developers and publishers.

## Credits

- [GARbro](https://github.com/morkt/GARbro) (MIT): reference for the INT archive encryption and the HG-3 format
- The archive layout, the image sections and the script format were checked against the games' own files
