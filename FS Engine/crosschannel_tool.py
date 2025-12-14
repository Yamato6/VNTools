#!/usr/bin/env python3
"""
CrossChannel MultiTool - Extractor/Empaquetador para archivos .PD (FS Engine)
Juego: CROSS†CHANNEL
"""

import os
import sys
import struct
from pathlib import Path


def read_uint64_le(f):
    """Lee un entero sin signo de 64 bits en little-endian."""
    return struct.unpack('<Q', f.read(8))[0]


def write_uint64_le(f, value):
    """Escribe un entero sin signo de 64 bits en little-endian."""
    f.write(struct.pack('<Q', value))


def extract(pd_file: str, out_dir: str):
    """Extrae todos los archivos de un archivo .PD"""
    
    with open(pd_file, 'rb') as f:
        # Leer modo (8 bytes)
        mode = f.read(8).decode('latin-1').rstrip('\x00')
        
        if mode not in ('PackOnly', 'PackPlus'):
            raise ValueError(f"Formato desconocido: {mode}")
        
        xor_byte = 0x00 if mode == 'PackOnly' else 0xFF
        
        # Saltar 56 bytes de padding
        padding = f.read(56)
        if padding != b'\x00' * 56:
            print("Advertencia: padding no es cero")
        
        # Leer cantidad de archivos
        file_count = read_uint64_le(f)
        
        print(f"Extrayendo {file_count} archivos desde {pd_file} ({mode})...")
        
        # Crear directorio de salida
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        
        for i in range(file_count):
            # Leer entrada del directorio (128 + 8 + 8 = 144 bytes)
            name_bytes = f.read(128)
            filename = name_bytes.decode('latin-1').rstrip('\x00')
            
            offset = read_uint64_le(f)
            length = read_uint64_le(f)
            
            # Guardar posición actual
            pos = f.tell()
            
            # Ir al offset y leer datos
            f.seek(offset)
            data = bytearray(f.read(length))
            
            # Aplicar XOR si es PackPlus
            if xor_byte != 0:
                for j in range(len(data)):
                    data[j] ^= xor_byte
            
            # Escribir archivo
            out_path = Path(out_dir) / filename
            out_path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(out_path, 'wb') as out_file:
                out_file.write(data)
            
            print(f" -> {filename} ({length} bytes)")
            
            # Volver a la posición del directorio
            f.seek(pos)
        
        print(f"Extracción completada en: {out_dir}")


def pack(in_dir: str, pd_file: str, mode: str):
    """Empaqueta archivos en un archivo .PD"""
    
    if mode not in ('PackOnly', 'PackPlus'):
        raise ValueError(f"Modo inválido: {mode}. Usa 'PackOnly' o 'PackPlus'")
    
    xor_byte = 0x00 if mode == 'PackOnly' else 0xFF
    
    # Obtener lista de archivos
    in_path = Path(in_dir)
    files = [f for f in in_path.iterdir() if f.is_file()]
    
    if not files:
        raise ValueError("Directorio vacío o inválido")
    
    print(f"Empaquetando {len(files)} archivos en {pd_file} ({mode})...")
    
    with open(pd_file, 'wb') as f:
        # Escribir header
        mode_bytes = mode.encode('latin-1')
        f.write(mode_bytes)
        f.write(b'\x00' * (8 - len(mode_bytes)))  # Padding del modo a 8 bytes
        f.write(b'\x00' * 56)  # 56 bytes de padding
        write_uint64_le(f, len(files))  # Cantidad de archivos
        
        # Calcular inicio de datos (después del directorio)
        directory_start = f.tell()
        directory_size = 144 * len(files)  # 128 + 8 + 8 por archivo
        data_start = directory_start + directory_size
        
        # Reservar espacio para el directorio
        f.write(b'\x00' * directory_size)
        
        # Escribir datos de archivos y guardar offsets/lengths
        entries = []
        
        for file_path in files:
            offset = f.tell()
            
            with open(file_path, 'rb') as src:
                data = bytearray(src.read())
            
            # Aplicar XOR si es PackPlus
            if xor_byte != 0:
                for j in range(len(data)):
                    data[j] ^= xor_byte
            
            f.write(data)
            
            entries.append({
                'name': file_path.name,
                'offset': offset,
                'length': len(data)
            })
            
            print(f" <- {file_path.name} ({len(data)} bytes)")
        
        # Volver al directorio y escribir entradas
        f.seek(directory_start)
        
        for entry in entries:
            # Nombre (128 bytes, padding con ceros)
            name_bytes = entry['name'].encode('latin-1')
            if len(name_bytes) > 128:
                raise ValueError(f"Nombre demasiado largo: {entry['name']}")
            
            f.write(name_bytes)
            f.write(b'\x00' * (128 - len(name_bytes)))
            
            # Offset y length
            write_uint64_le(f, entry['offset'])
            write_uint64_le(f, entry['length'])
        
        print(f"Paquete creado: {pd_file}")


def usage():
    print("CrossChannel MultiTool - Extractor/Empaquetador .PD")
    print()
    print("Uso:")
    print("  python crosschannel_tool.py extract <archivo.PD> <directorio_salida>")
    print("  python crosschannel_tool.py pack <directorio_entrada> <archivo.PD> <PackOnly|PackPlus>")
    print()
    print("Ejemplos:")
    print("  python crosschannel_tool.py extract cg.pd output_cg")
    print("  python crosschannel_tool.py pack output_cg nuevo.pd PackPlus")


def main():
    if len(sys.argv) < 2:
        usage()
        return
    
    cmd = sys.argv[1].lower()
    
    try:
        if cmd == 'extract':
            if len(sys.argv) != 4:
                usage()
                return
            extract(sys.argv[2], sys.argv[3])
        
        elif cmd == 'pack':
            if len(sys.argv) != 5:
                usage()
                return
            pack(sys.argv[2], sys.argv[3], sys.argv[4])
        
        else:
            usage()
    
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
