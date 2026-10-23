#!/usr/bin/env python3
"""
CrossChannel MultiTool - Extractor/Empaquetador para archivos .PD (FS Engine)
Juego: CROSS†CHANNEL

Sin argumentos abre la interfaz gráfica (crosschannel_gui.py).
"""

import sys

import pd_core


def print_progress(i, total, name):
    if name:
        print(f" [{i + 1}/{total}] {name}")


def cmd_list(pd_file: str):
    mode, entries, warnings = pd_core.read_index(pd_file)
    for w in warnings:
        print(w)
    print(f"{pd_file}: {len(entries)} archivos ({mode})")
    for e in entries:
        print(f" {e.size:>12}  {e.name}")
    print(f"Total: {sum(e.size for e in entries)} bytes")


def cmd_extract(pd_file: str, out_dir: str):
    print(f"Extrayendo desde {pd_file}...")
    mode, count = pd_core.extract(pd_file, out_dir, progress=print_progress)
    print(f"Extracción completada: {count} archivos ({mode}) en {out_dir}")


def cmd_pack(in_dir: str, pd_file: str, mode=None):
    mode, source = pd_core.resolve_mode(in_dir, mode)
    print(f"Empaquetando {in_dir} en {pd_file} ({mode}, {source})...")
    mode, count = pd_core.pack(in_dir, pd_file, mode, progress=print_progress)
    print(f"Paquete creado: {pd_file} ({count} archivos)")


def usage():
    print("CrossChannel MultiTool - Extractor/Empaquetador .PD")
    print()
    print("Uso:")
    print("  python crosschannel_tool.py                 (abre la interfaz gráfica)")
    print("  python crosschannel_tool.py list <archivo.PD>")
    print("  python crosschannel_tool.py extract <archivo.PD> <directorio_salida>")
    print("  python crosschannel_tool.py pack <directorio_entrada> <archivo.PD> [PackOnly|PackPlus]")
    print()
    print("Si se omite el modo en 'pack', se usa el de _pdinfo.json (creado al extraer)")
    print("o PackPlus por defecto.")
    print()
    print("Ejemplos:")
    print("  python crosschannel_tool.py extract cg.pd output_cg")
    print("  python crosschannel_tool.py pack output_cg nuevo.pd")


def main():
    # La consola puede usar cp932 u otra codificación: no abortar por un nombre
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, 'reconfigure'):
            stream.reconfigure(errors='replace')

    if len(sys.argv) < 2:
        try:
            import crosschannel_gui
        except ImportError as e:
            print(f"No se pudo abrir la interfaz gráfica: {e}")
            print("Instala las dependencias con: pip install -r requirements.txt")
            print()
            usage()
            return
        crosschannel_gui.main()
        return

    cmd = sys.argv[1].lower()
    args = sys.argv[2:]

    try:
        if cmd == 'list' and len(args) == 1:
            cmd_list(args[0])
        elif cmd == 'extract' and len(args) == 2:
            cmd_extract(args[0], args[1])
        elif cmd == 'pack' and len(args) in (2, 3):
            cmd_pack(args[0], args[1], args[2] if len(args) == 3 else None)
        else:
            usage()
    except KeyboardInterrupt:
        print("Cancelado")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
