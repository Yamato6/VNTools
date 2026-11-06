#!/usr/bin/env python3
"""
Yeti Tools GUI - Archivos .cpk, texturas XTX y guiones (motor Yeti/Regista)
Juego: CROSS†CHANNEL -Final Complete- (PC)
"""

import multiprocessing
import os
import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk
from PIL import Image

import yeti_codecs
import yeti_extract
import yeti_script
from yeti_common import Cancelled, check_cancel, report
from yeti_cpk import CPK
from yeti_xtx import FMT_ARGB4444, FMT_ARGB8888, FMT_DXT5, is_xtx, xtx_info, xtx_to_image

APP_TITLE = "Yeti Tools"
PREVIEW_SIZE = (300, 260)
PREVIEW_INTERVAL = 0.5      # segundos entre actualizaciones de la vista previa al extraer
PREVIEW_MAX_SIZE = 64 << 20
MAX_REPORT_ROWS = 5000
FORMAT_NAMES = {FMT_ARGB8888: "ARGB8888", FMT_ARGB4444: "ARGB4444", FMT_DXT5: "DXT5"}

MODE_RELOC = "Relocate (no limit)"
MODE_FIXED = "Fixed size"

REPORT_FILTERS = {
    "With warnings": lambda e: yeti_script.ST_CUT in e.flags or yeti_script.ST_UNSUPPORTED in e.flags,
    "Grew": lambda e: yeti_script.ST_GREW in e.flags,
    "Cut": lambda e: yeti_script.ST_CUT in e.flags,
    "Replaced characters": lambda e: yeti_script.ST_REPLACED in e.flags,
    "Unsupported characters": lambda e: yeti_script.ST_UNSUPPORTED in e.flags,
    "All modified": lambda e: True,
}


def format_size(size: int) -> str:
    for unit in ('B', 'KB', 'MB', 'GB'):
        if size < 1024 or unit == 'GB':
            return f"{size:.0f} {unit}" if unit == 'B' else f"{size:.1f} {unit}"
        size /= 1024


def open_folder(path):
    try:
        os.startfile(path)
    except (AttributeError, OSError):
        pass


def muted(widget_cls, master, **kw):
    return widget_cls(master, text_color=("gray35", "gray65"), **kw)


def clear_image(label: ctk.CTkLabel, text: str = ""):
    """
    Quita la imagen de un CTkLabel. configure(image=None) no la quita del label
    interno de Tk: este sigue apuntando a la imagen anterior, que se borra al
    liberarse, y el siguiente configure falla con "image ... does not exist".
    """
    label._label.configure(image="")
    label.configure(image=None, text=text)


def checkerboard(size, cell=8) -> Image.Image:
    """Fondo de cuadros para ver la transparencia."""
    w, h = size
    img = Image.new('RGBA', size, (204, 204, 204, 255))
    dark = Image.new('RGBA', (cell, cell), (153, 153, 153, 255))
    for y in range(0, h, cell):
        for x in range((y // cell) % 2 * cell, w, cell * 2):
            img.paste(dark, (x, y))
    return img


class PathRow:
    """Fila etiqueta + campo + botón de examinar."""

    def __init__(self, master, row, label, browse, button="Browse…", width=100):
        self.var = tk.StringVar()
        ctk.CTkLabel(master, text=label).grid(row=row, column=0, sticky="w", padx=(4, 8), pady=3)
        self.entry = ctk.CTkEntry(master, textvariable=self.var)
        self.entry.grid(row=row, column=1, sticky="ew", pady=3)
        self.button = ctk.CTkButton(master, text=button, width=width, command=browse)
        self.button.grid(row=row, column=2, padx=(8, 4), pady=3)

    def get(self):
        return self.var.get().strip().strip('"')

    def set(self, value):
        self.var.set(str(value))


class Preview:
    """Panel de vista previa: imagen sobre cuadros, ◀ ▶ entre varias imágenes e información."""

    def __init__(self, master):
        self.frame = ctk.CTkFrame(master, width=PREVIEW_SIZE[0] + 20)
        self.frame.grid_propagate(False)
        self.frame.grid_columnconfigure(0, weight=1)
        self.frame.grid_rowconfigure(0, weight=1)
        self.image = ctk.CTkLabel(self.frame, text="No preview")
        self.image.grid(row=0, column=0, sticky="nsew", padx=10, pady=(10, 4))
        nav = ctk.CTkFrame(self.frame, fg_color="transparent")
        nav.grid(row=1, column=0, sticky="ew", padx=10)
        nav.grid_columnconfigure(1, weight=1)
        self.prev = ctk.CTkButton(nav, text="◀", width=36, command=lambda: self.step(-1))
        self.prev.grid(row=0, column=0)
        self.count = ctk.CTkLabel(nav, text="")
        self.count.grid(row=0, column=1)
        self.next = ctk.CTkButton(nav, text="▶", width=36, command=lambda: self.step(1))
        self.next.grid(row=0, column=2)
        self.info = ctk.CTkLabel(self.frame, text="", justify="left", anchor="w",
                                 wraplength=PREVIEW_SIZE[0])
        self.info.grid(row=2, column=0, sticky="ew", padx=10, pady=(6, 10))
        self.header = ""
        self.items = []         # [(imagen o función que la devuelve, texto)]
        self.index = 0
        self._ref = None        # CTkImage mostrada (para que no se libere)
        self._render()

    def grid(self, **kw):
        self.frame.grid(**kw)

    def bind_keys(self, widget):
        """← → cambian de imagen."""
        widget.bind("<Left>", lambda _e: (self.step(-1), "break")[1])
        widget.bind("<Right>", lambda _e: (self.step(1), "break")[1])

    def show(self, header="", items=()):
        self.header, self.items, self.index = header, list(items), 0
        self._render()

    def show_file(self, path):
        try:
            with Image.open(path) as img:
                img.load()
                self.show(Path(path).name, [(img.copy(), f"{img.width}×{img.height}")])
        except OSError:
            pass

    def step(self, delta):
        if len(self.items) > 1:
            self.index = (self.index + delta) % len(self.items)
            self._render()

    def _render(self):
        lines = [self.header] if self.header else []
        img = None
        if self.items:
            source, text = self.items[self.index]
            try:
                img = source() if callable(source) else source
                lines.append(text)
            except Exception as ex:
                lines.append(f"Could not show it:\n{ex}")
        if img is None:
            self._ref = None
            clear_image(self.image, "No preview")
        else:
            thumb = img.convert('RGBA')
            thumb.thumbnail(PREVIEW_SIZE)
            bg = checkerboard(thumb.size)
            bg.alpha_composite(thumb)
            self._ref = ctk.CTkImage(light_image=bg, dark_image=bg, size=bg.size)
            self.image.configure(image=self._ref, text="")
        many = len(self.items) > 1
        self.prev.configure(state="normal" if many else "disabled")
        self.next.configure(state="normal" if many else "disabled")
        self.count.configure(text=f"{self.index + 1} / {len(self.items)}" if many else "")
        self.info.configure(text="\n".join(t for t in lines if t))


def convert_xtx_folder(folder, out_dir, split=False, progress=None, cancel=None) -> int:
    """Convierte las texturas .xtx sueltas de folder a PNG en out_dir."""
    files = sorted(p for p in Path(folder).iterdir() if p.is_file() and p.suffix.lower() == '.xtx')
    for i, path in enumerate(files):
        check_cancel(cancel)
        report(progress, i, len(files), path.name)
        yeti_extract.xtx_file_to_png(path, Path(out_dir) / f"{path.stem}.png", split)
    report(progress, len(files), len(files))
    return len(files)


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1000x720")
        self.minsize(820, 600)

        self.events = queue.Queue()
        self.cancel_event = threading.Event()
        self.worker = None
        self.busy_widgets = []
        self._autos = {}            # último valor puesto automáticamente en cada campo
        self._last_preview = 0.0

        self.entries = []           # pestaña CPK Files: lista de yeti_cpk.Entry
        self._cpk = None            # el .cpk abierto (para la vista previa)
        self._xtx_folder = None
        self.report = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.tabs = ctk.CTkTabview(self)
        self.tabs.grid(row=0, column=0, sticky="nsew", padx=12, pady=(6, 0))
        self._build_cpk_tab(self.tabs.add("CPK Files"))
        self._build_xtx_tab(self.tabs.add("XTX Textures"))
        self._build_script_tab(self.tabs.add("Scripts"))
        self._build_status_bar()
        self._apply_tree_style()
        self.after(50, self._poll_events)

    # ------------------------------------------------------------ helpers UI

    def _busy(self, *widgets):
        self.busy_widgets.extend(widgets)
        return widgets[0] if len(widgets) == 1 else widgets

    def _make_tree(self, master, columns, headings, widths, anchors=None):
        frame = ctk.CTkFrame(master, fg_color="transparent")
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(0, weight=1)
        tree = ttk.Treeview(frame, columns=columns[1:], selectmode="extended", style="Yeti.Treeview")
        for i, (col, head, width) in enumerate(zip(columns, headings, widths)):
            key = "#0" if i == 0 else col
            anchor = (anchors or {}).get(col, "w")
            tree.heading(key, text=head, anchor=anchor)
            tree.column(key, width=width, stretch=(i == 0 or width == 0), anchor=anchor)
        tree.grid(row=0, column=0, sticky="nsew")
        scroll = ctk.CTkScrollbar(frame, command=tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        tree.configure(yscrollcommand=scroll.set)
        return frame, tree

    def _section(self, master, row, title):
        frame = ctk.CTkFrame(master)
        frame.grid(row=row, column=0, columnspan=3, sticky="ew", padx=4, pady=(8, 0))
        frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(frame, text=title, font=ctk.CTkFont(weight="bold")) \
            .grid(row=0, column=0, sticky="w", padx=8, pady=(6, 0))
        return frame

    def _auto(self, row: PathRow, value):
        """Pone un valor por defecto salvo que el usuario haya escrito otro."""
        if row.get() in ("", self._autos.get(id(row), "")):
            row.set(value)
            self._autos[id(row)] = str(value)

    def _browse_dir(self, row: PathRow, title):
        path = filedialog.askdirectory(title=title)
        if path:
            row.set(path)

    # ------------------------------------------------------------ Pestaña CPK

    def _build_cpk_tab(self, tab):
        tab.grid_columnconfigure(1, weight=1)
        tab.grid_rowconfigure(3, weight=1)

        self.cpk_path = PathRow(tab, 0, ".cpk file:", self.browse_cpk, "Open…")
        self.cpk_path.entry.bind("<Return>", lambda _e: self.load_cpk(self.cpk_path.get()))
        self._busy(self.cpk_path.button)

        self.cpk_info = muted(ctk.CTkLabel, tab, text="No file open", anchor="w")
        self.cpk_info.grid(row=1, column=0, columnspan=3, sticky="ew", padx=4)

        self.cpk_filter = tk.StringVar()
        self.cpk_filter.trace_add("write", lambda *_: self._refresh_cpk_tree())
        ctk.CTkLabel(tab, text="Filter:").grid(row=2, column=0, sticky="w", padx=(4, 8), pady=3)
        ctk.CTkEntry(tab, textvariable=self.cpk_filter, placeholder_text="name contains…") \
            .grid(row=2, column=1, columnspan=2, sticky="ew", padx=(0, 4), pady=3)

        body = ctk.CTkFrame(tab, fg_color="transparent")
        body.grid(row=3, column=0, columnspan=3, sticky="nsew", padx=4, pady=4)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)
        frame, self.cpk_tree = self._make_tree(
            body, ("file", "size", "real", "comp"), ("File", "Size", "Uncompressed", "Compression"),
            (180, 85, 100, 85), {"size": "e", "real": "e"})
        frame.grid(row=0, column=0, sticky="nsew")
        self.cpk_tree.bind("<<TreeviewSelect>>", lambda _e: self._on_cpk_select())
        self.cpk_preview = Preview(body)
        self.cpk_preview.grid(row=0, column=1, sticky="ns", padx=(8, 0))
        self.cpk_preview.bind_keys(self.cpk_tree)

        self.cpk_out = PathRow(tab, 4, "Output:", lambda: self._browse_dir(self.cpk_out, "Output folder"))
        self._busy(self.cpk_out.button)

        actions = ctk.CTkFrame(tab, fg_color="transparent")
        actions.grid(row=5, column=0, columnspan=3, sticky="ew", padx=4, pady=(2, 0))
        self.resume_var = tk.BooleanVar(value=True)
        self._busy(ctk.CTkCheckBox(actions, text="Resume", variable=self.resume_var)).pack(side="left")
        ctk.CTkLabel(actions, text="Workers:").pack(side="left", padx=(12, 6))
        self.workers_var = tk.StringVar(value=str(yeti_extract.default_workers()))
        self._busy(ctk.CTkOptionMenu(actions, values=[str(n) for n in range(1, (os.cpu_count() or 1) + 1)],
                                     width=70, variable=self.workers_var)).pack(side="left")
        self.cpk_sel_label = muted(ctk.CTkLabel, actions, text="")
        self.cpk_sel_label.pack(side="left", padx=12)
        self._busy(ctk.CTkButton(actions, text="Extract game…", width=120,
                                 command=lambda: self.start_cpk_extract("game"))).pack(side="right")
        self._busy(ctk.CTkButton(actions, text="Extract all", width=110,
                                 command=lambda: self.start_cpk_extract("all"))).pack(side="right", padx=8)
        self._busy(ctk.CTkButton(actions, text="Extract selected", width=120,
                                 command=lambda: self.start_cpk_extract("selected"))).pack(side="right")
        muted(ctk.CTkLabel, tab, anchor="w",
              text="Textures are saved as PNG and the other files decompressed, in a folder per .cpk. "
                   "\"Extract game…\" also saves the audio.") \
            .grid(row=6, column=0, columnspan=3, sticky="ew", padx=4, pady=(2, 0))

        pack = self._section(tab, 7, "Pack")
        self.cpk_pack_in = PathRow(pack, 1, "Folder:", lambda: self._browse_dir(self.cpk_pack_in,
                                                                                 "Folder with the edited files"))
        self.cpk_pack_out = PathRow(pack, 2, ".cpk file:", self.browse_cpk_pack_out, "Save as…")
        self._busy(self.cpk_pack_in.button, self.cpk_pack_out.button)
        muted(ctk.CTkLabel, pack, anchor="w",
              text="Uses the open .cpk as the original: only the edited PNG and <id>.bin / <id>.hca files change.") \
            .grid(row=3, column=1, sticky="w")
        self._busy(ctk.CTkButton(pack, text="Pack", width=140, command=self.start_cpk_pack)) \
            .grid(row=3, column=2, sticky="e", padx=4, pady=(2, 8))

    def browse_cpk(self):
        path = filedialog.askopenfilename(title="Open .cpk file",
                                          filetypes=[("CPK files", "*.cpk"), ("All", "*.*")])
        if path:
            self.load_cpk(path)

    @staticmethod
    def _new_data_dir(cpk_path) -> Path:
        """<juego>/data_new junto a la carpeta data, para las salidas."""
        data = Path(cpk_path).parent
        return data.parent / f"{data.name}_new"

    def load_cpk(self, path):
        path = path.strip().strip('"')
        if not path:
            return
        try:
            cpk = CPK(path)
        except Exception as e:
            messagebox.showerror(APP_TITLE, f"Could not read the file:\n{e}")
            return
        if self._cpk is not None:
            self._cpk.close()
        self._cpk = cpk
        self.entries = cpk.entries
        self.cpk_path.set(path)
        p = Path(path)
        self._auto(self.cpk_out, p.parent.parent / f"{p.parent.name}_extracted")
        self._auto(self.cpk_pack_in, Path(self.cpk_out.get()) / p.stem)
        self._auto(self.cpk_pack_out, self._new_data_dir(path) / p.name)

        total = sum(e.size for e in self.entries)
        packed = sum(1 for e in self.entries if e.compressed)
        self.cpk_info.configure(text=f"CpkMode {cpk.mode}  ·  {len(self.entries)} files  ·  "
                                     f"{format_size(total)}  ·  {packed} with CRILAYLA")
        self._refresh_cpk_tree()
        self.cpk_preview.show()

    def _refresh_cpk_tree(self):
        self.cpk_tree.delete(*self.cpk_tree.get_children())
        text = self.cpk_filter.get().lower()
        for i, e in enumerate(self.entries):
            if text in e.path.lower():
                self.cpk_tree.insert("", "end", iid=str(i), text=e.path,
                                     values=(format_size(e.size), format_size(e.extract_size),
                                             "CRILAYLA" if e.compressed else ""))
        self._update_cpk_sel()

    def _update_cpk_sel(self):
        if not self.entries:
            self.cpk_sel_label.configure(text="")
            return
        self.cpk_sel_label.configure(
            text=f"{len(self.cpk_tree.get_children())} shown · {len(self.cpk_tree.selection())} selected")

    def _on_cpk_select(self):
        self._update_cpk_sel()
        sel = self.cpk_tree.selection()
        if len(sel) != 1:
            return
        e = self.entries[int(sel[0])]
        header = f"{e.path}\n{format_size(e.extract_size)}" + (" (CRILAYLA)" if e.compressed else "")
        if e.extract_size > PREVIEW_MAX_SIZE:
            self.cpk_preview.show(header)
            return
        try:
            data, lzss = yeti_extract.unpack_info(self._cpk, e)
            # Las texturas se decodifican al mostrarlas: un pak puede llevar muchas
            textures = [(name, blob) for name, blob in yeti_extract.leaves(e, data) if is_xtx(blob)]
        except Exception as ex:
            self.cpk_preview.show(f"{header}\n\nCould not read it:\n{ex}")
            return
        if lzss:
            header += " (LZSS)"
        items = []
        for name, blob in textures:
            fmt, w, h = xtx_info(blob)
            items.append((lambda b=blob: xtx_to_image(b),
                          f"{w}×{h}, {FORMAT_NAMES.get(fmt, f'format {fmt}')}\nSaved as {name}.png"))
        self.cpk_preview.show(header if items else f"{header}\nNo images", items)

    def start_cpk_extract(self, scope):
        out_dir = self.cpk_out.get()
        if scope != "game" and not self.entries:
            messagebox.showwarning(APP_TITLE, "Open a .cpk file first.")
            return
        if not out_dir:
            messagebox.showwarning(APP_TITLE, "Choose an output folder.")
            return
        kwargs = {"resume": self.resume_var.get(), "workers": int(self.workers_var.get())}
        if scope == "game":
            start = Path(self.cpk_path.get()).parent if self.cpk_path.get() else None
            src = filedialog.askdirectory(title="Game data folder", initialdir=start)
            if not src:
                return
            if not yeti_extract.find_cpks(src) and not yeti_extract.find_cpks(src, audio=True):
                messagebox.showwarning(APP_TITLE, "There are no .cpk files in that folder.")
                return

            def done(result):
                images, audio = result
                text = self._images_summary(images)
                if audio:
                    text += f"\n{audio.files} audio files" + (f" ({audio.wav} also as WAV)" if audio.wav else "")
                    if audio.errors:
                        text += f"\n{len(audio.errors)} WAV errors:\n" + "\n".join(audio.errors[:5])
                self._ask_open(f"{text}\n\nin:\n{out_dir}", out_dir)

            self.run_task(yeti_extract.extract_game, (src, out_dir), kwargs, "Extracting the game", done)
            return

        if scope == "selected":
            ids = [self.entries[int(i)].id for i in self.cpk_tree.selection()]
            if not ids:
                messagebox.showwarning(APP_TITLE, "No files selected.")
                return
            kwargs["ids"] = ids
        folder = Path(out_dir) / Path(self.cpk_path.get()).stem

        def done(r):
            self._auto(self.cpk_pack_in, folder)
            self._ask_open(f"{self._images_summary(r)}\n\nin:\n{folder}", folder)

        self.cpk_preview.show()
        self.run_task(yeti_extract.extract_images, (self.cpk_path.get(), out_dir), kwargs, "Extracting", done)

    @staticmethod
    def _images_summary(r) -> str:
        text = f"{r.images} images and {r.others} other files"
        if r.skipped:
            text += f" ({r.skipped} were already done)"
        if r.errors:
            text += f"\n\n{len(r.errors)} errors:\n" + "\n".join(r.errors[:10])
        return text

    def browse_cpk_pack_out(self):
        path = self._ask_save("Save .cpk", self.cpk_pack_out.get() or "new.cpk", ".cpk",
                              [("CPK files", "*.cpk"), ("All", "*.*")])
        if path:
            self.cpk_pack_out.set(path)

    def start_cpk_pack(self):
        cpk, folder, out = self.cpk_path.get(), self.cpk_pack_in.get(), self.cpk_pack_out.get()
        if not self.entries:
            messagebox.showwarning(APP_TITLE, "Open the original .cpk first.")
            return
        if not folder or not Path(folder).is_dir():
            messagebox.showwarning(APP_TITLE, "Choose the folder with the edited files.")
            return
        if not self._check_output(out, source=cpk):
            return
        try:
            yeti_extract.find_images_folder(folder, cpk)
            fn = yeti_extract.pack_images       # carpeta de una extracción: PNG editados
        except ValueError:
            fn = yeti_extract.pack_files        # <id>.bin / <id>.hca sueltos

        def done(r):
            if r.errors:
                messagebox.showerror(APP_TITLE, "The file was not created. Errors:\n\n" + "\n".join(r.errors[:15]))
                return
            changed = "\n".join(r.changed[:15]) + ("\n…" if len(r.changed) > 15 else "")
            self._ask_open(f"{len(r.changed)} of {r.entries} files replaced{':' if r.changed else '.'}\n"
                           f"{changed}\n\n{out}", Path(out).parent)

        self.run_task(fn, (cpk, folder, out), {}, "Packing", done)

    # ------------------------------------------------------------ Pestaña XTX

    def _build_xtx_tab(self, tab):
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)

        conv = self._section(tab, 0, "XTX → PNG")
        conv.grid_configure(pady=(4, 6))
        self.xtx_in = PathRow(conv, 1, "XTX folder:", self.browse_xtx_in)
        self.xtx_in.entry.bind("<Return>", lambda _e: self._list_xtx_folder(self.xtx_in.get()))
        self.xtx_out = PathRow(conv, 2, "PNG folder:", lambda: self._browse_dir(self.xtx_out, "PNG folder"))
        self._busy(self.xtx_in.button, self.xtx_out.button)
        self.xtx_split = tk.BooleanVar(value=False)
        self._busy(ctk.CTkCheckBox(conv, text="One PNG per channel (font48.xtx keeps a glyph sheet in each "
                                              "channel)", variable=self.xtx_split,
                                   command=self._on_xtx_select)).grid(row=3, column=1, sticky="w")
        self._busy(ctk.CTkButton(conv, text="Convert", width=140, command=self.start_xtx)) \
            .grid(row=3, column=2, sticky="e", padx=4, pady=(2, 8))

        body = ctk.CTkFrame(tab, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=4, pady=4)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)
        frame, self.xtx_tree = self._make_tree(body, ("name", "size"), ("Textures in the folder", "Size"),
                                               (380, 100), {"size": "e"})
        self.xtx_tree.configure(selectmode="browse")
        frame.grid(row=0, column=0, sticky="nsew")
        self.xtx_tree.bind("<<TreeviewSelect>>", lambda _e: self._on_xtx_select())
        self.xtx_preview = Preview(body)
        self.xtx_preview.grid(row=0, column=1, sticky="ns", padx=(8, 0))
        self.xtx_preview.bind_keys(self.xtx_tree)

    def browse_xtx_in(self):
        path = filedialog.askdirectory(title="Folder with .xtx textures")
        if path:
            self.xtx_in.set(path)
            self._auto(self.xtx_out, Path(path).parent / f"{Path(path).name}_png")
            self._list_xtx_folder(path)

    def _list_xtx_folder(self, folder):
        self.xtx_tree.delete(*self.xtx_tree.get_children())
        self.xtx_preview.show()
        self._xtx_folder = Path(folder) if folder else None
        if not self._xtx_folder or not self._xtx_folder.is_dir():
            return
        for f in sorted(self._xtx_folder.iterdir()):
            if f.is_file() and f.suffix.lower() == '.xtx':
                self.xtx_tree.insert("", "end", iid=f.name, text=f.name, values=(format_size(f.stat().st_size),))

    def _on_xtx_select(self):
        sel = self.xtx_tree.selection()
        if not sel or not self._xtx_folder:
            return
        try:
            data = (self._xtx_folder / sel[0]).read_bytes()
            fmt, w, h = xtx_info(data)
            img = xtx_to_image(data)
        except Exception as e:
            self.xtx_preview.show(sel[0], [(None, f"Could not read it:\n{e}")])
            return
        info = f"{w}×{h}, {FORMAT_NAMES.get(fmt, f'format {fmt}')}"
        if self.xtx_split.get():
            channels = dict(zip("rgba", img.split()))
            self.xtx_preview.show(sel[0], [(channels[c], f"{info}\nChannel {c.upper()}: _{c}.png")
                                           for c in "argb"])
        else:
            self.xtx_preview.show(sel[0], [(img, info)])

    def start_xtx(self):
        in_dir, out_dir = self.xtx_in.get(), self.xtx_out.get()
        if not in_dir or not Path(in_dir).is_dir() or not out_dir:
            messagebox.showwarning(APP_TITLE, "Choose the XTX folder and the PNG folder.")
            return
        self.run_task(convert_xtx_folder, (in_dir, out_dir), {"split": self.xtx_split.get()}, "Converting",
                      lambda n: self._ask_open(f"Converted {n} textures to:\n{out_dir}", out_dir))

    # ------------------------------------------------------------ Pestaña Scripts

    def _build_script_tab(self, tab):
        tab.grid_columnconfigure(1, weight=1)
        tab.grid_rowconfigure(7, weight=1)

        self.sn_path = PathRow(tab, 0, "sn.bin:", self.browse_sn, "Open…")
        self.sn_text = PathRow(tab, 1, "Text folder:", lambda: self._browse_dir(self.sn_text, "Text folder"))
        self.sn_charmap = PathRow(tab, 2, "Character table:", self.browse_charmap)
        self._busy(self.sn_path.button, self.sn_text.button, self.sn_charmap.button)

        ext = ctk.CTkFrame(tab)
        ext.grid(row=3, column=0, columnspan=3, sticky="ew", padx=4, pady=(6, 4))
        ctk.CTkLabel(ext, text="Extract", font=ctk.CTkFont(weight="bold")).pack(side="left", padx=8, pady=6)
        self.sn_listing = tk.BooleanVar(value=False)
        self._busy(ctk.CTkCheckBox(ext, text="Also save the full listing of each script",
                                   variable=self.sn_listing)).pack(side="left", padx=8)
        self._busy(ctk.CTkButton(ext, text="Extract text", width=140, command=self.start_sn_extract)) \
            .pack(side="right", padx=8, pady=6)

        reb = ctk.CTkFrame(tab)
        reb.grid(row=4, column=0, columnspan=3, sticky="ew", padx=4, pady=(0, 4))
        reb.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(reb, text="Rebuild", font=ctk.CTkFont(weight="bold")) \
            .grid(row=0, column=0, sticky="w", padx=8, pady=(6, 0))
        self.sn_out = PathRow(reb, 1, "Output sn.bin:", self.browse_sn_out, "Save as…")
        self._busy(self.sn_out.button)
        ctk.CTkLabel(reb, text="Mode:").grid(row=2, column=0, sticky="w", padx=(4, 8), pady=3)
        self.sn_mode = tk.StringVar(value=MODE_RELOC)
        self._busy(ctk.CTkSegmentedButton(reb, values=[MODE_RELOC, MODE_FIXED], variable=self.sn_mode)) \
            .grid(row=2, column=1, sticky="w", pady=3)
        self._busy(ctk.CTkButton(reb, text="Rebuild", width=140, command=self.start_sn_rebuild)) \
            .grid(row=2, column=2, sticky="e", padx=4, pady=(3, 8))
        muted(ctk.CTkLabel, reb, justify="left", anchor="w",
              text="Relocate: texts can be longer than the original (every jump, switch and choice is moved).\n"
                   "Fixed size: each text keeps its original size; longer texts are cut.") \
            .grid(row=3, column=0, columnspan=3, sticky="ew", padx=8, pady=(0, 6))

        bar = ctk.CTkFrame(tab, fg_color="transparent")
        bar.grid(row=5, column=0, columnspan=3, sticky="ew", padx=4)
        self.report_label = muted(ctk.CTkLabel, bar, text="The report will appear here after rebuilding.",
                                  anchor="w", justify="left", wraplength=640)
        self.report_label.pack(side="left", fill="x", expand=True)
        self.report_filter = ctk.CTkOptionMenu(bar, values=list(REPORT_FILTERS), width=200,
                                               command=lambda _v: self._refresh_report())
        self.report_filter.pack(side="right")

        frame, self.report_tree = self._make_tree(
            tab, ("script", "id", "bytes", "state", "detail"),
            ("Script", "ID", "Bytes", "Status", "Detail"), (120, 70, 90, 200, 0))
        frame.grid(row=7, column=0, columnspan=3, sticky="nsew", padx=4, pady=4)

    def browse_sn(self):
        path = filedialog.askopenfilename(title="Script file (sn.bin)",
                                          filetypes=[("sn.bin", "*.bin"), ("All", "*.*")])
        if path:
            self.sn_path.set(path)
            data = Path(path).parent
            self._auto(self.sn_text, data.parent / "text")
            self._auto(self.sn_out, self._new_data_dir(path) / Path(path).name)

    def browse_charmap(self):
        path = filedialog.askopenfilename(title="Character table (optional)",
                                          filetypes=[("Text", "*.txt"), ("All", "*.*")])
        if path:
            self.sn_charmap.set(path)

    def browse_sn_out(self):
        path = self._ask_save("Save sn.bin", self.sn_out.get() or "sn.bin", ".bin",
                              [("sn.bin", "*.bin"), ("All", "*.*")])
        if path:
            self.sn_out.set(path)

    def _load_charmap(self):
        """Devuelve (ok, charmap)."""
        path = self.sn_charmap.get()
        if not path:
            return True, None
        try:
            return True, yeti_script.CharMap.load(path)
        except Exception as e:
            messagebox.showerror(APP_TITLE, f"Error in the character table:\n{e}")
            return False, None

    def start_sn_extract(self):
        sn, folder = self.sn_path.get(), self.sn_text.get()
        if not sn or not Path(sn).is_file():
            messagebox.showwarning(APP_TITLE, "Choose sn.bin.")
            return
        if not folder:
            messagebox.showwarning(APP_TITLE, "Choose the text folder.")
            return
        ok, charmap = self._load_charmap()
        if not ok:
            return

        def done(result):
            files, strings = result
            self.report_label.configure(text=f"{strings} lines from {files} scripts. One file per script: "
                                             "ID|ADDRESS|TEXT; translate only the text.")
            self._ask_open(f"Extracted {strings} lines from {files} scripts to:\n{folder}", folder)

        self.run_task(yeti_script.extract_strings, (sn, folder),
                      {"charmap": charmap, "with_listing": self.sn_listing.get()}, "Extracting text", done)

    def start_sn_rebuild(self):
        sn, folder, out = self.sn_path.get(), self.sn_text.get(), self.sn_out.get()
        if not sn or not Path(sn).is_file():
            messagebox.showwarning(APP_TITLE, "Choose the original sn.bin.")
            return
        if not folder or not Path(folder).is_dir():
            messagebox.showwarning(APP_TITLE, "Choose the translated text folder.")
            return
        if not self._check_output(out, source=sn):
            return
        ok, charmap = self._load_charmap()
        if not ok:
            return
        mode = 'reloc' if self.sn_mode.get() == MODE_RELOC else 'fixed'

        def done(rep):
            self.report = rep
            text = rep.summary()
            if rep.warnings:
                text += "\n" + " · ".join(rep.warnings[:3])
            self.report_label.configure(text=text)
            self._refresh_report()
            problems = sum(1 for e in rep.entries if REPORT_FILTERS["With warnings"](e))
            msg = f"Script created:\n{out}\n\n{rep.summary()}"
            if problems:
                msg += f"\n\n{problems} lines with warnings: check the report."
            self._ask_open(msg, Path(out).parent)

        self.run_task(yeti_script.rebuild, (sn, folder, out), {"charmap": charmap, "mode": mode},
                      "Rebuilding", done)

    def _refresh_report(self):
        self.report_tree.delete(*self.report_tree.get_children())
        if not self.report:
            return
        keep = REPORT_FILTERS[self.report_filter.get()]
        rows = [e for e in self.report.entries if keep(e)]
        for i, e in enumerate(rows[:MAX_REPORT_ROWS]):
            self.report_tree.insert("", "end", iid=str(i), text=e.script,
                                    values=(f"{e.sid:04d}", f"{e.old_len} → {e.new_len}",
                                            ", ".join(e.flags), e.detail))
        if len(rows) > MAX_REPORT_ROWS:
            self.report_tree.insert("", "end", iid="more", text="…",
                                    values=("", "", f"{len(rows) - MAX_REPORT_ROWS} more", ""))

    # ------------------------------------------------------------ Comunes

    def _ask_save(self, title, current, ext, types):
        initial = Path(current)
        return filedialog.asksaveasfilename(
            title=title, defaultextension=ext, initialfile=initial.name,
            initialdir=str(initial.parent) if initial.parent.exists() else None, filetypes=types)

    def _check_output(self, out_file, source=None):
        if not out_file:
            messagebox.showwarning(APP_TITLE, "Choose the output file.")
            return False
        out_path = Path(out_file).resolve()
        if source and out_path == Path(source).resolve():
            messagebox.showwarning(APP_TITLE, "The output cannot be the original file.")
            return False
        if out_path.exists() and not messagebox.askyesno(APP_TITLE, f"{out_path.name} already exists.\nOverwrite it?"):
            return False
        return True

    def _ask_open(self, msg, folder):
        if messagebox.askyesno(APP_TITLE, f"{msg}\n\nOpen the folder?"):
            open_folder(folder)

    def _build_status_bar(self):
        bar = ctk.CTkFrame(self)
        bar.grid(row=1, column=0, sticky="ew", padx=12, pady=12)
        bar.grid_columnconfigure(1, weight=1)

        self.progress = ctk.CTkProgressBar(bar)
        self.progress.set(0)
        self.progress.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(10, 4))
        ready = "Ready" if yeti_codecs.native_available() else \
            f"Ready ({yeti_codecs.LIB_NAME} not found: CRILAYLA in Python, much slower)"
        self.status = ctk.CTkLabel(bar, text=ready, anchor="w")
        self.status.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 8))

        self.cancel_btn = ctk.CTkButton(bar, text="Cancel", width=90, state="disabled",
                                        fg_color="#b23b3b", hover_color="#8f2f2f",
                                        command=self.cancel_task)
        self.cancel_btn.grid(row=0, column=2, rowspan=2, padx=(0, 10))
        ctk.CTkOptionMenu(bar, values=["System", "Light", "Dark"], width=100,
                          command=self._change_theme).grid(row=0, column=3, rowspan=2, padx=(0, 10))

    def _change_theme(self, value):
        ctk.set_appearance_mode({"System": "system", "Light": "light", "Dark": "dark"}[value])
        self.after(10, self._apply_tree_style)

    def _apply_tree_style(self):
        dark = ctk.get_appearance_mode() == "Dark"
        bg, fg = ("#2b2b2b", "#dce4ee") if dark else ("#ffffff", "#1a1a1a")
        head_bg = "#3a3a3a" if dark else "#e5e5e5"
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Yeti.Treeview", background=bg, fieldbackground=bg, foreground=fg,
                        rowheight=24, borderwidth=0)
        style.map("Yeti.Treeview", background=[("selected", "#1f6aa5")], foreground=[("selected", "#ffffff")])
        style.configure("Yeti.Treeview.Heading", background=head_bg, foreground=fg, relief="flat")
        style.map("Yeti.Treeview.Heading", background=[("active", head_bg)])

    # ------------------------------------------------------------ Tareas

    def run_task(self, fn, args, kwargs, label, on_done):
        if self.worker and self.worker.is_alive():
            return
        self.cancel_event.clear()
        self._set_busy(True)
        self.progress.set(0)
        self.status.configure(text=f"{label}…")
        self._task_label = label
        self._task_done = on_done

        def target():
            try:
                result = fn(*args, progress=self._report, cancel=self.cancel_event, **kwargs)
                self.events.put(("done", result))
            except Cancelled:
                self.events.put(("cancelled", None))
            except Exception as e:
                self.events.put(("error", e))

        self.worker = threading.Thread(target=target, daemon=True)
        self.worker.start()

    def _report(self, i, total, name):
        # Llamado desde el hilo de trabajo: solo encolar
        self.events.put(("progress", (i, total, name)))

    def _maybe_preview(self, name):
        """Muestra el último PNG escrito, como mucho cada PREVIEW_INTERVAL segundos."""
        if not name.lower().endswith(".png") or time.time() - self._last_preview < PREVIEW_INTERVAL:
            return
        self._last_preview = time.time()
        self.cpk_preview.show_file(name)

    def _poll_events(self):
        try:
            for _ in range(500):
                kind, data = self.events.get_nowait()
                if kind == "progress":
                    i, total, name = data
                    self.progress.set(i / total if total else 1)
                    if name:
                        self.status.configure(text=f"{self._task_label} [{min(i + 1, total)}/{total}]  "
                                                   f"{Path(name).name}")
                        self._maybe_preview(name)
                elif kind == "done":
                    self._finish("Done")
                    self.progress.set(1)
                    self._task_done(data)
                elif kind == "cancelled":
                    self._finish("Cancelled")
                elif kind == "error":
                    self._finish("Error")
                    messagebox.showerror(APP_TITLE, str(data))
        except queue.Empty:
            pass
        self.after(50, self._poll_events)

    def _finish(self, text):
        self._set_busy(False)
        self.status.configure(text=text)

    def cancel_task(self):
        self.cancel_event.set()
        self.status.configure(text="Cancelling… (finishing the files in progress)")

    def _set_busy(self, busy):
        state = "disabled" if busy else "normal"
        for w in self.busy_widgets:
            w.configure(state=state)
        self.cancel_btn.configure(state="normal" if busy else "disabled")


def main():
    ctk.set_appearance_mode("system")
    ctk.set_default_color_theme("blue")
    App().mainloop()


if __name__ == '__main__':
    multiprocessing.freeze_support()
    main()
