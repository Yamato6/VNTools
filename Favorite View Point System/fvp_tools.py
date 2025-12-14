#!/usr/bin/env python3
"""
FVP Tools - Tools for the Favorite View Point System visual novel engine
Extracts/packs BIN archives, converts NVSG images, and handles HCB scripts
"""

import struct
import zlib
import sys
import re
from pathlib import Path
from io import BytesIO
from typing import Dict, List, Tuple, Optional

try:
    from PIL import Image
    import numpy as np
except ImportError:
    print("Error: Requires Pillow and NumPy")
    print("Install with: pip install pillow numpy")
    sys.exit(1)


# =============================================================================
# HCB Script Tool - Opcodes and VM definitions
# =============================================================================

# Opcode argument types
OPARG_NULL = 0      # No arguments
OPARG_X32 = 1       # 32-bit offset/address
OPARG_I32 = 2       # 32-bit signed integer
OPARG_I16 = 3       # 16-bit signed integer
OPARG_I8 = 4        # 8-bit signed integer
OPARG_I8I8 = 5      # Two 8-bit integers
OPARG_STRING = 6    # Length-prefixed string

# HCB Opcode definitions: (opcode, name, arg_type)
HCB_OPCODES = [
    (0x00, "nop", OPARG_NULL),
    (0x01, "initstack", OPARG_I8I8),
    (0x02, "call", OPARG_X32),
    (0x03, "syscall", OPARG_I16),
    (0x04, "ret", OPARG_NULL),
    (0x05, "ret2", OPARG_NULL),
    (0x06, "jmp", OPARG_X32),
    (0x07, "jmpcond", OPARG_X32),
    (0x08, "pushtrue", OPARG_NULL),
    (0x09, "pushfalse", OPARG_NULL),
    (0x0A, "pushint", OPARG_I32),
    (0x0B, "pushint", OPARG_I16),
    (0x0C, "pushint", OPARG_I8),
    (0x0D, "pushfloat", OPARG_X32),
    (0x0E, "pushstring", OPARG_STRING),
    (0x0F, "pushglobal", OPARG_I16),
    (0x10, "pushstack", OPARG_I8),
    (0x11, "unk_11", OPARG_I16),
    (0x12, "unk_12", OPARG_I8),
    (0x13, "pushtop", OPARG_NULL),
    (0x14, "pushtemp", OPARG_NULL),
    (0x15, "popglobal", OPARG_I16),
    (0x16, "copystack", OPARG_I8),
    (0x17, "unk_17", OPARG_I16),
    (0x18, "unk_18", OPARG_I8),
    (0x19, "neg", OPARG_NULL),
    (0x1A, "add", OPARG_NULL),
    (0x1B, "sub", OPARG_NULL),
    (0x1C, "mul", OPARG_NULL),
    (0x1D, "div", OPARG_NULL),
    (0x1E, "mod", OPARG_NULL),
    (0x1F, "test", OPARG_NULL),
    (0x20, "logand", OPARG_NULL),
    (0x21, "logor", OPARG_NULL),
    (0x22, "eq", OPARG_NULL),
    (0x23, "neq", OPARG_NULL),
    (0x24, "gt", OPARG_NULL),
    (0x25, "le", OPARG_NULL),
    (0x26, "lt", OPARG_NULL),
    (0x27, "ge", OPARG_NULL),
]

HCB_LAST_OPCODE = 0x27

def get_opcode_info(opcode: int) -> Tuple[str, int]:
    """Returns (name, arg_type) for an opcode."""
    if opcode > HCB_LAST_OPCODE:
        return (None, None)
    return (HCB_OPCODES[opcode][1], HCB_OPCODES[opcode][2])

def get_opcode_by_name(name: str) -> List[Tuple[int, int]]:
    """Returns list of (opcode, arg_type) matching the name."""
    matches = []
    for op, n, arg in HCB_OPCODES:
        if n == name:
            matches.append((op, arg))
    return matches


# =============================================================================
# HCB Decoder - Decompiles HCB bytecode to readable text
# =============================================================================

def hcb_decode(hcb_path: str, output_path: str, strings_path: Optional[str] = None):
    """
    Decompiles an HCB script file to readable text format.
    Optionally extracts strings to a separate file for translation.
    
    HCB format notes:
    - First 4 bytes: entry point offset (also marks end of code section)
    - Code section: bytes 4 to entry_point
    - String format: 1 byte length + string data (NOT 2 bytes!)
    """
    hcb_path = Path(hcb_path)
    output_path = Path(output_path)
    
    with open(hcb_path, 'rb') as f:
        data = f.read()
    
    if len(data) < 4:
        raise ValueError("File too small to be valid HCB")
    
    # Entry point is at offset stored in first 4 bytes
    entry_point = struct.unpack_from('<I', data, 0)[0]
    code_end = entry_point  # Code section ends at entry point offset
    
    print(f"HCB file: {hcb_path.name}")
    print(f"  Size: {len(data)} bytes")
    print(f"  Entry point: 0x{entry_point:08X}")
    print(f"  Code section: 0x0004 - 0x{code_end:08X}")
    
    # First pass: find all function starts (initstack) and jump targets
    functions: Dict[int, int] = {}  # addr -> func_number
    labels: Dict[int, str] = {}     # addr -> label_name
    
    pos = 4
    func_num = 0
    
    while pos < code_end:
        if pos >= len(data):
            break
        opcode = data[pos]
        if opcode > HCB_LAST_OPCODE:
            pos += 1
            continue
        
        name, arg_type = get_opcode_info(opcode)
        
        # Mark function starts
        if name == "initstack":
            functions[pos] = func_num
            func_num += 1
        
        # Calculate instruction size and collect jump targets
        pos += 1
        if arg_type == OPARG_NULL:
            pass
        elif arg_type == OPARG_X32:
            if pos + 4 <= len(data):
                if name in ("jmp", "jmpcond", "call"):
                    target = struct.unpack_from('<I', data, pos)[0]
                    if target < code_end and target not in labels and target not in functions:
                        labels[target] = f"label_{target:08x}"
            pos += 4
        elif arg_type == OPARG_I32:
            pos += 4
        elif arg_type == OPARG_I16:
            pos += 2
        elif arg_type == OPARG_I8:
            pos += 1
        elif arg_type == OPARG_I8I8:
            pos += 2
        elif arg_type == OPARG_STRING:
            # String format: 1 byte length + string data
            if pos < len(data):
                str_len = data[pos]
                pos += 1 + str_len
            else:
                pos += 1
    
    print(f"  Functions: {len(functions)}")
    print(f"  Labels: {len(labels)}")
    
    # Second pass: decode to text
    lines = []
    strings_list = []
    string_id = 0
    pos = 4
    current_func = -1
    
    while pos < code_end:
        if pos >= len(data):
            break
            
        # Check for function start
        if pos in functions:
            if current_func >= 0:
                lines.append("")  # Blank line between functions
            current_func = functions[pos]
            lines.append(f"# ===== FUNCTION {current_func} =====")
        
        # Check for label
        if pos in labels:
            lines.append(f"{labels[pos]}:")
        
        inst_addr = pos
        opcode = data[pos]
        
        if opcode > HCB_LAST_OPCODE:
            pos += 1
            continue
        
        name, arg_type = get_opcode_info(opcode)
        pos += 1
        
        # Decode arguments
        if arg_type == OPARG_NULL:
            lines.append(f"  {name}")
        
        elif arg_type == OPARG_X32:
            if pos + 4 > len(data):
                break
            val = struct.unpack_from('<I', data, pos)[0]
            pos += 4
            if name in ("jmp", "jmpcond"):
                target_label = labels.get(val, functions.get(val))
                if target_label is not None:
                    if isinstance(target_label, int):
                        lines.append(f"  {name} FUNCTION_{target_label}")
                    else:
                        lines.append(f"  {name} {target_label}")
                else:
                    lines.append(f"  {name} 0x{val:08X}")
            elif name == "call":
                func_id = functions.get(val)
                if func_id is not None:
                    lines.append(f"  {name} FUNCTION_{func_id}")
                else:
                    lines.append(f"  {name} 0x{val:08X}")
            elif name == "pushfloat":
                float_val = struct.unpack('<f', struct.pack('<I', val))[0]
                lines.append(f"  {name} {float_val}")
            else:
                lines.append(f"  {name} 0x{val:08X}")
        
        elif arg_type == OPARG_I32:
            if pos + 4 > len(data):
                break
            val = struct.unpack_from('<i', data, pos)[0]
            pos += 4
            lines.append(f"  {name} {val}")
        
        elif arg_type == OPARG_I16:
            if pos + 2 > len(data):
                break
            val = struct.unpack_from('<h', data, pos)[0]
            pos += 2
            lines.append(f"  {name} {val}")
        
        elif arg_type == OPARG_I8:
            if pos >= len(data):
                break
            val = struct.unpack_from('<b', data, pos)[0]
            pos += 1
            lines.append(f"  {name} {val}")
        
        elif arg_type == OPARG_I8I8:
            if pos + 2 > len(data):
                break
            val1 = struct.unpack_from('<b', data, pos)[0]
            val2 = struct.unpack_from('<b', data, pos + 1)[0]
            pos += 2
            lines.append(f"  {name} {val1}, {val2}")
        
        elif arg_type == OPARG_STRING:
            # String format: 1 byte length + string data
            if pos >= len(data):
                break
            str_len = data[pos]
            pos += 1
            if pos + str_len > len(data):
                break
            try:
                string = data[pos:pos + str_len].decode('cp932', errors='replace').rstrip('\x00')
            except:
                string = data[pos:pos + str_len].decode('utf-8', errors='replace').rstrip('\x00')
            pos += str_len
            
            # Escape special characters for text output
            escaped = string.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\r', '\\r')
            lines.append(f'  {name} "{escaped}"  ; [STR_{string_id:04d}]')
            strings_list.append((string_id, inst_addr, string))
            string_id += 1
    
    # Add entry point info
    lines.append("")
    lines.append(f"# ENTRY_POINT: 0x{entry_point:08X}")
    
    # Write output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    
    print(f"  Output: {output_path}")
    print(f"  Strings found: {len(strings_list)}")
    
    # Write strings file if requested
    if strings_path:
        strings_path = Path(strings_path)
        with open(strings_path, 'w', encoding='utf-8') as f:
            for sid, addr, text in strings_list:
                # Format: ID|ADDRESS|TEXT (one per line)
                escaped = text.replace('\\', '\\\\').replace('\n', '\\n').replace('\r', '\\r')
                f.write(f"{sid:04d}|0x{addr:08X}|{escaped}\n")
        print(f"  Strings file: {strings_path}")
    
    return len(functions), len(strings_list)


# =============================================================================
# HCB Rebuilder - Compiles text back to HCB bytecode
# =============================================================================

def hcb_rebuild(original_hcb: str, strings_path: str, output_hcb: str):
    """
    Rebuilds an HCB file with replaced strings.
    Reads original HCB, replaces strings from strings file, writes new HCB.
    """
    original_hcb = Path(original_hcb)
    strings_path = Path(strings_path)
    output_hcb = Path(output_hcb)
    
    # Read original HCB
    with open(original_hcb, 'rb') as f:
        data = bytearray(f.read())
    
    # Read replacement strings
    replacements: Dict[int, str] = {}  # addr -> new_string
    with open(strings_path, 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(f, 1):
            line = line.rstrip('\n\r')
            if not line or line.startswith('#'):
                continue
            
            parts = line.split('|', 2)
            if len(parts) < 3:
                print(f"  [WARN] Invalid line {line_num}: {line[:50]}")
                continue
            
            try:
                sid = int(parts[0])
                addr = int(parts[1], 16) if parts[1].startswith('0x') else int(parts[1])
                text = parts[2]
                # Unescape
                text = text.replace('\\n', '\n').replace('\\r', '\r').replace('\\\\', '\\')
                replacements[addr] = text
            except ValueError as e:
                print(f"  [WARN] Parse error line {line_num}: {e}")
    
    print(f"HCB rebuild: {original_hcb.name}")
    print(f"  Replacements: {len(replacements)}")
    
    # Validate Shift-JIS compatibility (game engine requirement)
    encoding_warnings = 0
    for addr, text in replacements.items():
        try:
            text.encode('cp932', errors='strict')
        except UnicodeEncodeError as e:
            if encoding_warnings < 10:  # Limit warnings
                print(f"  [WARN] 0x{addr:08X}: Character not in Shift-JIS: {e.object[e.start:e.end]!r}")
            encoding_warnings += 1
    if encoding_warnings > 10:
        print(f"  ... and {encoding_warnings - 10} more encoding warnings")
    if encoding_warnings > 0:
        print(f"  [INFO] {encoding_warnings} strings have unsupported characters - will keep originals")
    
    if not replacements:
        # No replacements, just copy
        output_hcb.parent.mkdir(parents=True, exist_ok=True)
        with open(output_hcb, 'wb') as f:
            f.write(data)
        print(f"  [WARN] No replacements found, copied original")
        return
    
    # Get entry point
    entry_point = struct.unpack_from('<I', data, 0)[0]
    code_end = entry_point
    
    # Build new code section with replaced strings (keeping same sizes)
    new_code = bytearray()
    
    pos = 4
    while pos < code_end:
        old_addr = pos
        
        opcode = data[pos]
        if opcode > HCB_LAST_OPCODE:
            new_code.append(opcode)
            pos += 1
            continue
        
        name, arg_type = get_opcode_info(opcode)
        new_code.append(opcode)
        pos += 1
        
        if arg_type == OPARG_NULL:
            pass
        
        elif arg_type == OPARG_X32:
            # Copy, will fix up later
            new_code.extend(data[pos:pos + 4])
            pos += 4
        
        elif arg_type == OPARG_I32:
            new_code.extend(data[pos:pos + 4])
            pos += 4
        
        elif arg_type == OPARG_I16:
            new_code.extend(data[pos:pos + 2])
            pos += 2
        
        elif arg_type == OPARG_I8:
            new_code.append(data[pos])
            pos += 1
        
        elif arg_type == OPARG_I8I8:
            new_code.extend(data[pos:pos + 2])
            pos += 2
        
        elif arg_type == OPARG_STRING:
            old_str_len = data[pos]  # 1 byte length
            pos += 1
            old_str = data[pos:pos + old_str_len]
            pos += old_str_len
            
            # Check for replacement
            if old_addr in replacements:
                try:
                    # Strict Shift-JIS encoding - game engine only supports this
                    new_str = replacements[old_addr].encode('cp932', errors='strict')
                except UnicodeEncodeError as e:
                    print(f"  WARNING: String at 0x{old_addr:08X} contains characters not supported by Shift-JIS")
                    print(f"           Keeping original string. Problem char: {e.object[e.start:e.end]!r}")
                    new_str = bytes(old_str)
                
                # Ensure null terminator
                if not new_str.endswith(b'\x00'):
                    new_str = new_str + b'\x00'
                
                # IMPORTANT: Keep same size to avoid address shifting
                # Pad with spaces or truncate to match original length
                if len(new_str) < old_str_len:
                    # Pad with spaces before null terminator
                    padding = old_str_len - len(new_str)
                    new_str = new_str[:-1] + (b' ' * padding) + b'\x00'
                elif len(new_str) > old_str_len:
                    # Truncate (keep null at end)
                    new_str = new_str[:old_str_len - 1] + b'\x00'
            else:
                new_str = bytes(old_str)
            
            new_code.append(len(new_str))  # 1 byte length (same as original)
            new_code.extend(new_str)
    
    # Since we keep string sizes fixed, entry point stays the same
    # and no address fix-up is needed
    
    # Build final output - just replace code section, keep rest intact
    output_data = bytearray()
    output_data.extend(struct.pack('<I', entry_point))  # Same entry point
    output_data.extend(new_code)
    output_data.extend(data[entry_point:])  # Copy data section unchanged
    
    # Write output
    output_hcb.parent.mkdir(parents=True, exist_ok=True)
    with open(output_hcb, 'wb') as f:
        f.write(output_data)
    
    print(f"  Original size: {len(data)} bytes")
    print(f"  New size: {len(output_data)} bytes")
    print(f"  Output: {output_hcb}")


def hcb_extract_strings(hcb_path: str, output_path: str):
    """
    Extracts only the strings from an HCB file for translation.
    Simpler alternative to full decompilation when you only need strings.
    """
    hcb_path = Path(hcb_path)
    output_path = Path(output_path)
    
    with open(hcb_path, 'rb') as f:
        data = f.read()
    
    entry_point = struct.unpack_from('<I', data, 0)[0]
    code_end = entry_point
    
    strings_list = []
    string_id = 0
    pos = 4
    
    while pos < code_end:
        opcode = data[pos]
        if opcode > HCB_LAST_OPCODE:
            pos += 1
            continue
        
        name, arg_type = get_opcode_info(opcode)
        inst_addr = pos
        pos += 1
        
        if arg_type == OPARG_NULL:
            pass
        elif arg_type == OPARG_X32:
            pos += 4
        elif arg_type == OPARG_I32:
            pos += 4
        elif arg_type == OPARG_I16:
            pos += 2
        elif arg_type == OPARG_I8:
            pos += 1
        elif arg_type == OPARG_I8I8:
            pos += 2
        elif arg_type == OPARG_STRING:
            str_len = data[pos]  # 1 byte length
            pos += 1
            try:
                string = data[pos:pos + str_len].decode('cp932', errors='replace').rstrip('\x00')
            except:
                string = data[pos:pos + str_len].decode('utf-8', errors='replace').rstrip('\x00')
            pos += str_len
            
            strings_list.append((string_id, inst_addr, string))
            string_id += 1
    
    # Write strings file
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        for sid, addr, text in strings_list:
            escaped = text.replace('\\', '\\\\').replace('\n', '\\n').replace('\r', '\\r')
            f.write(f"{sid:04d}|0x{addr:08X}|{escaped}\n")
    
    print(f"Extracted {len(strings_list)} strings from {hcb_path.name}")
    print(f"  Output: {output_path}")
    
    return len(strings_list)


def hcb_split_strings(strings_path: str):
    """
    Splits a strings file into parts based on <part> tags.
    
    Format in strings file:
    <part name="Chapter 1" filename="chapter1.txt">
    0001|0x00001234|Text here
    0002|0x00001256|More text
    </part>
    <part name="Chapter 2" filename="chapter2.txt">
    ...
    </part>
    """
    strings_path = Path(strings_path)
    base_dir = strings_path.parent
    
    with open(strings_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Find all parts
    part_pattern = re.compile(
        r'<part\s+(?:name="([^"]*)")?\s*(?:filename="([^"]*)")?\s*>(.*?)</part>',
        re.DOTALL | re.IGNORECASE
    )
    
    parts = part_pattern.findall(content)
    
    if not parts:
        print(f"No <part> tags found in {strings_path.name}")
        print("Format: <part name=\"Part Name\" filename=\"output.txt\">...strings...</part>")
        return 0
    
    files_created = 0
    for name, filename, part_content in parts:
        if not filename:
            print(f"  [WARN] Part '{name}' has no filename, skipping")
            continue
        
        output_file = base_dir / filename
        output_file.parent.mkdir(parents=True, exist_ok=True)
        
        # Clean up content - remove leading/trailing whitespace per line
        lines = [line.strip() for line in part_content.strip().split('\n') if line.strip()]
        
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(f"# Part: {name}\n")
            f.write('\n'.join(lines))
            f.write('\n')
        
        print(f"  Created: {filename} ({len(lines)} lines)")
        files_created += 1
    
    print(f"Split {strings_path.name} into {files_created} files")
    return files_created


def hcb_merge_strings(build_script_path: str, output_path: str):
    """
    Merges multiple string files into one using a build script.
    
    Build script format:
    <part filename="chapter1.txt">
    <part filename="chapter2.txt">
    <part filename="chapter3.txt">
    
    Or directly list files (one per line):
    chapter1.txt
    chapter2.txt
    chapter3.txt
    """
    build_script_path = Path(build_script_path)
    output_path = Path(output_path)
    base_dir = build_script_path.parent
    
    with open(build_script_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Find files to merge
    files_to_merge = []
    
    # Try <part filename="..."> format first
    part_pattern = re.compile(r'<part\s+filename="([^"]+)"', re.IGNORECASE)
    matches = part_pattern.findall(content)
    
    if matches:
        files_to_merge = matches
    else:
        # Try plain file list (one per line)
        for line in content.split('\n'):
            line = line.strip()
            if line and not line.startswith('#') and not line.startswith('<'):
                files_to_merge.append(line)
    
    if not files_to_merge:
        print(f"No files found in build script {build_script_path.name}")
        return 0
    
    # Merge all files
    all_strings = []
    for filename in files_to_merge:
        file_path = base_dir / filename
        if not file_path.exists():
            print(f"  [WARN] File not found: {filename}")
            continue
        
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    all_strings.append(line)
        
        print(f"  Merged: {filename}")
    
    # Write output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(f"# Merged from {len(files_to_merge)} files\n")
        f.write('\n'.join(all_strings))
        f.write('\n')
    
    print(f"Merged {len(files_to_merge)} files -> {output_path.name} ({len(all_strings)} strings)")
    return len(all_strings)


# =============================================================================
# Format detection by magic bytes
# =============================================================================

MAGIC_EXTENSIONS = {
    b'OggS': '.ogg',      # Ogg Vorbis audio
    b'RIFF': '.wav',      # WAV audio
    b'hzc1': '',          # NVSG (no extension, engine requirement)
    b'\x89PNG': '.png',   # PNG
    b'\xff\xd8\xff': '.jpg',  # JPEG
}

def detect_extension(data: bytes) -> str:
    """Detects the appropriate extension based on magic bytes."""
    for magic, ext in MAGIC_EXTENSIONS.items():
        if data.startswith(magic):
            return ext
    return ''  # No extension by default


# =============================================================================
# BIN Tool - Extractor/Packer for .bin archives
# =============================================================================

def bin_extract(bin_path: str, output_folder: str, auto_ext: bool = True):
    """Extracts files from a .bin archive"""
    bin_path = Path(bin_path)
    output_folder = Path(output_folder)
    output_folder.mkdir(parents=True, exist_ok=True)

    with open(bin_path, 'rb') as f:
        file_count = struct.unpack('<I', f.read(4))[0]
        file_names_size = struct.unpack('<I', f.read(4))[0]
        
        table_size = file_count * 12
        file_names_start = 8 + table_size

        # Read file table
        entries = []
        for i in range(file_count):
            name_offset, offset, size = struct.unpack('<III', f.read(12))
            entries.append((name_offset, offset, size))

        # Extract files
        for i, (name_offset, offset, size) in enumerate(entries):
            # Read filename (Shift_JIS, null-terminated)
            f.seek(file_names_start + name_offset)
            name_bytes = b''
            while True:
                b = f.read(1)
                if b == b'\x00' or not b:
                    break
                name_bytes += b
            
            name = name_bytes.decode('shift_jis', errors='replace')
            output_name = f"{i:04d}_{name}"

            # Read content
            f.seek(offset)
            content = f.read(size)

            # Auto-detect extension
            if auto_ext:
                ext = detect_extension(content)
                output_name += ext

            # Save file
            output_path = output_folder / output_name
            output_path.write_bytes(content)
            print(f"-> {output_path.name}")

    print(f"\n[OK] Extraction complete: {file_count} files")


def bin_pack(input_folder: str, bin_path: str):
    """Packs files from a folder into a .bin archive"""
    input_folder = Path(input_folder)
    bin_path = Path(bin_path)

    # Get sorted files
    files = sorted([f for f in input_folder.iterdir() if f.is_file()])
    if not files:
        print("Error: Empty folder")
        return

    # Prepare names (remove numeric prefix, encode as Shift_JIS)
    names = []
    for f in files:
        # Remove "0000_" prefix
        clean_name = f.name
        if clean_name[4:5] == '_' and clean_name[:4].isdigit():
            clean_name = clean_name[5:]
        names.append(clean_name.encode('shift_jis', errors='replace') + b'\x00')

    file_count = len(files)
    table_size = file_count * 12
    names_size = sum(len(n) for n in names)
    file_names_start = 8 + table_size

    with open(bin_path, 'wb') as out:
        # Header
        out.write(struct.pack('<I', file_count))
        out.write(struct.pack('<I', names_size))
        
        # Placeholder for table
        out.write(b'\x00' * table_size)

        # Write names
        name_offsets = []
        for name in names:
            name_offsets.append(out.tell() - file_names_start)
            out.write(name)

        # Write files and save offsets
        file_entries = []
        for i, f in enumerate(files):
            print(f"-> {f.name}")
            file_offset = out.tell()
            content = f.read_bytes()
            out.write(content)
            file_entries.append((name_offsets[i], file_offset, len(content)))

        # Write table at the beginning
        out.seek(8)
        for name_off, file_off, file_size in file_entries:
            out.write(struct.pack('<III', name_off, file_off, file_size))

    print(f"\n[OK] Packing complete: {bin_path.name}")


# =============================================================================
# NVSG Tool - NVSG to PNG image converter
# =============================================================================

def nvsg_decode(nvsg_path: str, png_path: str) -> dict:
    """Converts NVSG to PNG. Returns metadata."""
    nvsg_path = Path(nvsg_path)
    png_path = Path(png_path)

    with open(nvsg_path, 'rb') as f:
        # hzc1 header
        magic = f.read(4)
        if magic != b'hzc1':
            raise ValueError(f"Not a valid NVSG file (magic: {magic})")
        
        uncompressed_size = struct.unpack('<I', f.read(4))[0]
        header_size = struct.unpack('<I', f.read(4))[0]

        # NVSG header
        nvsg_magic = f.read(4)
        if nvsg_magic != b'NVSG':
            raise ValueError("Missing NVSG header")

        always_256 = struct.unpack('<H', f.read(2))[0]
        fmt = struct.unpack('<H', f.read(2))[0]
        width = struct.unpack('<H', f.read(2))[0]
        height = struct.unpack('<H', f.read(2))[0]
        x = struct.unpack('<H', f.read(2))[0]
        y = struct.unpack('<H', f.read(2))[0]
        unk1 = struct.unpack('<H', f.read(2))[0]
        unk2 = struct.unpack('<H', f.read(2))[0]
        image_count = struct.unpack('<I', f.read(4))[0]
        unk3 = struct.unpack('<I', f.read(4))[0]
        unk4 = struct.unpack('<I', f.read(4))[0]

        # Compressed data
        compressed = f.read()
        data = zlib.decompress(compressed)

    # Create image based on format
    if fmt == 0:  # BGR 24-bit
        img = Image.frombytes('RGB', (width, height), data, 'raw', 'BGR')
    elif fmt == 1:  # BGRA 32-bit
        img = Image.frombytes('RGBA', (width, height), data, 'raw', 'BGRA')
    elif fmt == 2:  # BGRA with multiple frames
        total_height = height * image_count
        img = Image.frombytes('RGBA', (width, total_height), data, 'raw', 'BGRA')
    elif fmt == 3:  # Grayscale
        img = Image.frombytes('L', (width, height), data)
    else:
        raise ValueError(f"Unsupported format: {fmt}")

    img.save(png_path, 'PNG')

    metadata = {
        'x': x, 'y': y, 'image_count': image_count if image_count > 0 else 1,
        'width': width, 'height': height, 'format': fmt
    }
    print(f"Decoded {nvsg_path.name} -> {png_path.name} "
          f"(x={x}, y={y}, count={image_count}, {width}x{height}, fmt={fmt})")
    return metadata


def nvsg_encode(png_path: str, nvsg_path: str, x: int, y: int, image_count: int = 1):
    """Converts PNG to NVSG."""
    png_path = Path(png_path)
    nvsg_path = Path(nvsg_path)

    img = Image.open(png_path)
    width, height = img.size

    # Determine format
    has_alpha = img.mode == 'RGBA'
    if image_count > 1:
        fmt = 2
        height //= image_count
    elif has_alpha:
        fmt = 1
    else:
        fmt = 0
        if img.mode != 'RGB':
            img = img.convert('RGB')

    # Convert to BGRA/BGR bytes
    if fmt in (1, 2):
        if img.mode != 'RGBA':
            img = img.convert('RGBA')
        data = img.tobytes('raw', 'BGRA')
    else:
        data = img.tobytes('raw', 'BGR')

    # Compress
    compressed = zlib.compress(data, level=9)

    with open(nvsg_path, 'wb') as f:
        # hzc1 header
        f.write(b'hzc1')
        f.write(struct.pack('<I', len(data)))
        f.write(struct.pack('<I', 0x20))  # header size
        
        # NVSG header
        f.write(b'NVSG')
        f.write(struct.pack('<H', 256))   # always 256
        f.write(struct.pack('<H', fmt))
        f.write(struct.pack('<H', width))
        f.write(struct.pack('<H', height))
        f.write(struct.pack('<H', x))
        f.write(struct.pack('<H', y))
        f.write(struct.pack('<H', 0))     # unk1
        f.write(struct.pack('<H', 0))     # unk2
        f.write(struct.pack('<I', image_count))
        f.write(struct.pack('<I', 0))     # unk3
        f.write(struct.pack('<I', 0))     # unk4
        
        f.write(compressed)

    print(f"Encoded {png_path.name} -> {nvsg_path.name} "
          f"(x={x}, y={y}, count={image_count}, {width}x{height}, fmt={fmt})")


# =============================================================================
# Batch conversion
# =============================================================================

def batch_decode(input_folder: str, output_folder: str):
    """Converts all NVSG files in a folder to PNG."""
    input_folder = Path(input_folder)
    output_folder = Path(output_folder)
    output_folder.mkdir(parents=True, exist_ok=True)
    
    log_path = output_folder / "decode_log.txt"
    log_entries = []

    for f in sorted(input_folder.iterdir()):
        if f.is_file():
            png_name = f.stem + ".png"
            png_path = output_folder / png_name
            try:
                meta = nvsg_decode(str(f), str(png_path))
                log_entries.append(
                    f"{png_name} x={meta['x']} y={meta['y']} "
                    f"image_count={meta['image_count']} width={meta['width']} "
                    f"height={meta['height']} format={meta['format']}"
                )
            except Exception as e:
                print(f"[ERROR] {f.name}: {e}")

    log_path.write_text('\n'.join(log_entries), encoding='utf-8')
    print(f"\n[OK] Log saved: {log_path}")


def batch_encode(input_folder: str, output_folder: str, log_path: str):
    """Converts all PNG files to NVSG using metadata from log."""
    input_folder = Path(input_folder)
    output_folder = Path(output_folder)
    log_path = Path(log_path)
    output_folder.mkdir(parents=True, exist_ok=True)

    # Parse log
    log_map = {}
    for line in log_path.read_text(encoding='utf-8').splitlines():
        parts = line.split()
        if len(parts) >= 4:
            png_name = parts[0]
            vals = {}
            for p in parts[1:]:
                if '=' in p:
                    k, v = p.split('=')
                    vals[k] = int(v)
            log_map[png_name] = vals

    for f in sorted(input_folder.glob('*.png')):
        if f.name in log_map:
            vals = log_map[f.name]
            out_path = output_folder / f.stem
            nvsg_encode(str(f), str(out_path), vals['x'], vals['y'], vals.get('image_count', 1))
        else:
            print(f"[WARN] No log entry for: {f.name}")


# =============================================================================
# CLI
# =============================================================================

def print_usage():
    print("""
FVP Tools - Tools for Favorite View Point System

Usage:
  BIN Archive:
    python fvp_tools.py bin-extract <file.bin> <output_folder> [--no-ext]
    python fvp_tools.py bin-pack <input_folder> <file.bin>
  
  NVSG Images:
    python fvp_tools.py nvsg-decode <nvsg_file> <png_file>
    python fvp_tools.py nvsg-encode <png_file> <nvsg_file> --x <N> --y <N> [--count <N>]
  
  Batch Operations:
    python fvp_tools.py batch-decode <nvsg_folder> <png_folder>
    python fvp_tools.py batch-encode <png_folder> <nvsg_folder> <decode_log.txt>
  
  HCB Scripts:
    python fvp_tools.py hcb-decode <file.hcb> <output.txt> [--strings <strings.txt>]
    python fvp_tools.py hcb-strings <file.hcb> <strings.txt>
    python fvp_tools.py hcb-rebuild <original.hcb> <strings.txt> <output.hcb>
    python fvp_tools.py hcb-split <strings.txt>
    python fvp_tools.py hcb-merge <build_script.txt> <output_strings.txt>

Options:
  --no-ext    Do not add automatic extension (for NVSG files)
  --strings   Also export strings to separate file for translation
  
Note: NVSG files have no extension (engine requirement).
      Audio files (OGG/WAV) are detected automatically.
      HCB strings use Shift-JIS (CP932) encoding.
""")


def main():
    args = sys.argv[1:]
    
    if len(args) < 1:
        print_usage()
        return

    cmd = args[0].lower()

    try:
        if cmd == 'bin-extract' and len(args) >= 3:
            auto_ext = '--no-ext' not in args
            bin_extract(args[1], args[2], auto_ext=auto_ext)
        
        elif cmd == 'bin-pack' and len(args) >= 3:
            bin_pack(args[1], args[2])
        
        elif cmd == 'nvsg-decode' and len(args) >= 3:
            nvsg_decode(args[1], args[2])
        
        elif cmd == 'nvsg-encode' and len(args) >= 3:
            # Parse optional arguments
            x, y, count = 0, 0, 1
            i = 3
            while i < len(args):
                if args[i] == '--x' and i + 1 < len(args):
                    x = int(args[i + 1]); i += 2
                elif args[i] == '--y' and i + 1 < len(args):
                    y = int(args[i + 1]); i += 2
                elif args[i] == '--count' and i + 1 < len(args):
                    count = int(args[i + 1]); i += 2
                else:
                    i += 1
            nvsg_encode(args[1], args[2], x, y, count)
        
        elif cmd == 'batch-decode' and len(args) >= 3:
            batch_decode(args[1], args[2])
        
        elif cmd == 'batch-encode' and len(args) >= 4:
            batch_encode(args[1], args[2], args[3])
        
        elif cmd == 'hcb-decode' and len(args) >= 3:
            # Parse optional --strings argument
            strings_path = None
            i = 3
            while i < len(args):
                if args[i] == '--strings' and i + 1 < len(args):
                    strings_path = args[i + 1]
                    i += 2
                else:
                    i += 1
            hcb_decode(args[1], args[2], strings_path)
        
        elif cmd == 'hcb-strings' and len(args) >= 3:
            hcb_extract_strings(args[1], args[2])
        
        elif cmd == 'hcb-rebuild' and len(args) >= 4:
            hcb_rebuild(args[1], args[2], args[3])
        
        elif cmd == 'hcb-split' and len(args) >= 2:
            hcb_split_strings(args[1])
        
        elif cmd == 'hcb-merge' and len(args) >= 3:
            hcb_merge_strings(args[1], args[2])
        
        else:
            print_usage()
    
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
