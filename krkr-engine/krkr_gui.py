#!/usr/bin/env python3
"""
KiriKiri Tools GUI - Archivos .xp3, imágenes TLG, sprites por capas y texto del motor KiriKiri 2
Probado con Noble Works (Yuzusoft, 2010).
"""

import multiprocessing
import os
import queue
import sqlite3
import threading
import time
import tkinter as tk
from collections import Counter
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk
from PIL import Image

import krkr_extract
import krkr_script
from krkr_common import Cancelled
from krkr_image import (compose, decode, image_info, info_file, is_layer_sheet, kind_of, layer_file,
                        parse_info, parse_layers, sprite_variants)
from krkr_psb import to_json
from krkr_tlg import native_available
from krkr_xp3 import WARNING_TEXT, Xp3, find_xp3, next_patch_name

APP_TITLE = "KiriKiri Tools"
PREVIEW_SIZE = (300, 260)
PREVIEW_INTERVAL = 0.5      # segundos entre actualizaciones de la vista previa al extraer
PREVIEW_MAX_SIZE = 64 << 20
MAX_REPORT_ROWS = 5000
ALL_TYPES = "All"
XP3_TYPES = [("XP3 files", "*.xp3"), ("All", "*.*")]
KIND_LABELS = {'tlg': 'image', 'png': 'image', 'jpg': 'image', 'bmp': 'image', 'ogg': 'audio', 'wav': 'audio',
               'loop': 'loop', 'psb': 'psb', 'sdb': 'database', 'video': 'video', 'font': 'font',
               'text': 'text', 'data': 'data'}
# Tipo por la extensión, para no leer cada archivo de un .xp3 grande (voice.xp3)
KIND_BY_EXT = {'.tlg': 'tlg', '.png': 'png', '.jpg': 'jpg', '.bmp': 'bmp', '.ogg': 'ogg', '.wav': 'wav',
               '.sli': 'loop', '.psb': 'psb', '.sdb': 'sdb', '.wmv': 'video', '.mpg': 'video',
               '.ks': 'text', '.tjs': 'text', '.csv': 'text', '.ini': 'text', '.func': 'text'}

REPORT_FILTERS = {
    "All": lambda e: True,
    "Changed": lambda e: krkr_script.ST_CHANGED in e.flags,
    "Unknown lines": lambda e: krkr_script.ST_UNKNOWN in e.flags,
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


def _text_sample(data: bytes, lines: int = 8) -> str:
    if data[:2] in (b'\xff\xfe', b'\xfe\xff'):
        text = data.decode('utf-16', 'replace')
    else:
        try:
            text = data.decode('utf-8')
        except UnicodeDecodeError:
            text = data.decode('cp932', 'replace')
    return "\n".join(line[:60] for line in text.splitlines()[:lines])


def sprite_preview(x: Xp3, by_name: dict, sheet_name: str):
    """Variantes compuestas (cargadas al mostrarlas) de una tabla de capas de sprites."""
    sheet = parse_layers(x.read(by_name[sheet_name]))
    info_e = by_name.get(info_file(sheet_name))
    if info_e is None:
        return f"Layer table: {len(sheet.layers)} layers, {sheet.width}×{sheet.height}", []
    info = parse_info(x.read(info_e))
    cache = {}

    def load(layer):
        if layer.layer_id not in cache:
            e = by_name.get(layer_file(sheet_name, layer))
            cache[layer.layer_id] = decode(x.read(e)) if e else None
        return cache[layer.layer_id]

    items = [((lambda layers=layers: compose(sheet, layers, load)), label)
             for label, layers in sprite_variants(info)]
    return (f"Layered sprite: {len(info.dresses)} outfits × {len(info.faces)} faces, "
            f"{sheet.width}×{sheet.height}"), items


def entry_preview(x: Xp3, by_name: dict, e):
    """(texto, [(imagen, texto)]) para la vista previa de un archivo del .xp3."""
    head = x.head(e, 64)
    if is_layer_sheet(e.name, head):
        return sprite_preview(x, by_name, e.name)
    kind = kind_of(e.name, head)
    if kind in ('tlg', 'png', 'jpg', 'bmp'):
        data = x.read(e)
        info = image_info(data)
        desc = f"{info[0]}×{info[1]}, {info[2]}-bit {info[3]}" if info else kind.upper()
        if kind == 'tlg':
            desc += f"\nSaved as {Path(krkr_extract.png_name(e.name)).name}"
        return "", [(decode(data), desc)]
    if kind == 'psb':
        return "PSB (also saved as .json)\n\n" + "\n".join(to_json(x.read(e)).splitlines()[:14]), []
    if kind == 'sdb':
        con = sqlite3.connect(':memory:')
        try:
            con.deserialize(x.read(e))
            tables = [r[0] for r in con.execute("select name from sqlite_master where type='table'")]
            rows = [f"{t}: {con.execute(f'select count(*) from {t}').fetchone()[0]} rows" for t in tables]
        finally:
            con.close()
        return "SQLite database\n" + "\n".join(rows), []
    if kind in ('text', 'loop'):
        return f"{KIND_LABELS[kind].capitalize()}\n\n{_text_sample(x.read(e))}", []
    return KIND_LABELS.get(kind, kind).capitalize(), []


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

        self.entries = []           # pestaña XP3 Files: lista de krkr_xp3.Xp3Entry
        self.kinds = []             # tipo de cada entrada
        self._xp3 = None            # el .xp3 abierto (para la vista previa)
        self._by_name = {}
        self.report = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.tabs = ctk.CTkTabview(self)
        self.tabs.grid(row=0, column=0, sticky="nsew", padx=12, pady=(6, 0))
        self._build_xp3_tab(self.tabs.add("XP3 Files"))
        self._build_text_tab(self.tabs.add("Scripts"))
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
        tree = ttk.Treeview(frame, columns=columns[1:], selectmode="extended", style="Krkr.Treeview")
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

    @staticmethod
    def _patch_path(game) -> Path:
        """<carpeta del juego>_new/patchN.xp3 (el siguiente parche libre), junto al juego."""
        game = Path(game)
        return game.parent / f"{game.name}_new" / next_patch_name(game)

    # ------------------------------------------------------------ Pestaña XP3

    def _build_xp3_tab(self, tab):
        tab.grid_columnconfigure(1, weight=1)
        tab.grid_rowconfigure(3, weight=1)

        self.xp3_path = PathRow(tab, 0, ".xp3 file:", self.browse_xp3, "Open…")
        self.xp3_path.entry.bind("<Return>", lambda _e: self.load_xp3(self.xp3_path.get()))
        self._busy(self.xp3_path.button)

        self.xp3_info = muted(ctk.CTkLabel, tab, text="No file open", anchor="w", justify="left")
        self.xp3_info.grid(row=1, column=0, columnspan=3, sticky="ew", padx=4)

        self.xp3_filter = tk.StringVar()
        self.xp3_filter.trace_add("write", lambda *_: self._refresh_xp3_tree())
        ctk.CTkLabel(tab, text="Filter:").grid(row=2, column=0, sticky="w", padx=(4, 8), pady=3)
        filt = ctk.CTkFrame(tab, fg_color="transparent")
        filt.grid(row=2, column=1, columnspan=2, sticky="ew", padx=(0, 4), pady=3)
        filt.grid_columnconfigure(0, weight=1)
        ctk.CTkEntry(filt, textvariable=self.xp3_filter, placeholder_text="name contains…") \
            .grid(row=0, column=0, sticky="ew")
        self.xp3_type = tk.StringVar(value=ALL_TYPES)
        self.xp3_type_menu = ctk.CTkOptionMenu(filt, values=[ALL_TYPES], width=100, variable=self.xp3_type,
                                               command=lambda _v: self._refresh_xp3_tree())
        self.xp3_type_menu.grid(row=0, column=1, padx=(8, 0))

        body = ctk.CTkFrame(tab, fg_color="transparent")
        body.grid(row=3, column=0, columnspan=3, sticky="nsew", padx=4, pady=4)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)
        frame, self.xp3_tree = self._make_tree(body, ("name", "type", "size"), ("Name", "Type", "Size"),
                                               (300, 70, 100), {"size": "e"})
        frame.grid(row=0, column=0, sticky="nsew")
        self.xp3_tree.bind("<<TreeviewSelect>>", lambda _e: self._on_xp3_select())
        self.xp3_preview = Preview(body)
        self.xp3_preview.grid(row=0, column=1, sticky="ns", padx=(8, 0))
        self.xp3_preview.bind_keys(self.xp3_tree)

        self.xp3_out = PathRow(tab, 4, "Output:", lambda: self._browse_dir(self.xp3_out, "Output folder"))
        self._busy(self.xp3_out.button)

        actions = ctk.CTkFrame(tab, fg_color="transparent")
        actions.grid(row=5, column=0, columnspan=3, sticky="ew", padx=4, pady=(2, 0))
        self.resume_var = tk.BooleanVar(value=True)
        self._busy(ctk.CTkCheckBox(actions, text="Resume", variable=self.resume_var)).pack(side="left")
        self.composed_var = tk.BooleanVar(value=True)
        self._busy(ctk.CTkCheckBox(actions, text="Compose sprites", variable=self.composed_var)) \
            .pack(side="left", padx=(12, 0))
        ctk.CTkLabel(actions, text="Workers:").pack(side="left", padx=(12, 6))
        self.workers_var = tk.StringVar(value=str(krkr_extract.default_workers()))
        self._busy(ctk.CTkOptionMenu(actions, values=[str(n) for n in range(1, (os.cpu_count() or 1) + 1)],
                                     width=70, variable=self.workers_var)).pack(side="left")
        self.xp3_sel_label = muted(ctk.CTkLabel, actions, text="")
        self.xp3_sel_label.pack(side="left", padx=12)
        self._busy(ctk.CTkButton(actions, text="Extract game…", width=120,
                                 command=lambda: self.start_xp3_extract("game"))).pack(side="right")
        self._busy(ctk.CTkButton(actions, text="Extract all", width=110,
                                 command=lambda: self.start_xp3_extract("all"))).pack(side="right", padx=8)
        self._busy(ctk.CTkButton(actions, text="Extract selected", width=120,
                                 command=lambda: self.start_xp3_extract("selected"))).pack(side="right")
        muted(ctk.CTkLabel, tab, anchor="w",
              text="TLG images are saved as PNG, PSB also as .json and the rest as is, in a folder per .xp3. "
                   "Layered sprites are composed into _composed.") \
            .grid(row=6, column=0, columnspan=3, sticky="ew", padx=4, pady=(2, 0))

        pack = self._section(tab, 7, "Pack")
        self.xp3_pack_in = PathRow(pack, 1, "Folder:", lambda: self._browse_dir(self.xp3_pack_in,
                                                                                 "Folder with the files"))
        self.xp3_pack_out = PathRow(pack, 2, "Patch .xp3:", self.browse_xp3_pack_out, "Save as…")
        self._busy(self.xp3_pack_in.button, self.xp3_pack_out.button)
        self._busy(ctk.CTkButton(pack, text="Pack", width=140, command=self.start_xp3_pack)) \
            .grid(row=3, column=2, sticky="e", padx=4, pady=(2, 8))
        muted(ctk.CTkLabel, pack, anchor="w", justify="left",
              text="Makes a patch with the files edited since the extraction (edited TLG images go back to TLG); "
                   "any other folder\ngoes in whole. Copy the patch to the game folder. If it already exists, "
                   "its other files are kept.") \
            .grid(row=4, column=0, columnspan=3, sticky="ew", padx=8, pady=(0, 6))

    def browse_xp3(self):
        path = filedialog.askopenfilename(title="Open .xp3 file", filetypes=XP3_TYPES)
        if path:
            self.load_xp3(path)

    def load_xp3(self, path):
        path = path.strip().strip('"')
        if not path:
            return
        try:
            x = Xp3(path)
            files = x.files()
            kinds = []
            for e in files:
                k = KIND_BY_EXT.get(os.path.splitext(e.name)[1].lower())
                kinds.append(k or kind_of(e.name, x.head(e, 16)))
        except Exception as e:
            messagebox.showerror(APP_TITLE, f"Could not read the file:\n{e}")
            return
        if self._xp3 is not None:
            self._xp3.close()
        self._xp3 = x
        self._by_name = {e.name: e for e in x.entries}
        self.entries = files
        self.kinds = [KIND_LABELS.get(k, k) for k in kinds]
        self.xp3_path.set(path)
        p = Path(path)
        self._auto(self.xp3_out, p.parent.parent / f"{p.parent.name}_extracted")
        self._auto(self.xp3_pack_in, Path(self.xp3_out.get()) / p.stem)
        self._auto(self.xp3_pack_out, self._patch_path(p.parent))
        self._auto(self.tx_game, p.parent)

        types = Counter(self.kinds)
        total = sum(e.size for e in files)
        text = (f"{len(files)} files  ·  {format_size(total)}  ·  "
                + ", ".join(f"{n} {k}" for k, n in types.most_common()))
        if x.has_warning:
            text += f"\n{WARNING_TEXT}"
        self.xp3_info.configure(text=text)
        self.xp3_type_menu.configure(values=[ALL_TYPES] + sorted(types))
        self.xp3_type.set(ALL_TYPES)
        self._refresh_xp3_tree()
        self.xp3_preview.show()

    def _refresh_xp3_tree(self):
        self.xp3_tree.delete(*self.xp3_tree.get_children())
        text = self.xp3_filter.get().lower()
        kind = self.xp3_type.get()
        for i, (e, k) in enumerate(zip(self.entries, self.kinds)):
            if text in e.name.lower() and kind in (ALL_TYPES, k):
                self.xp3_tree.insert("", "end", iid=str(i), text=e.name, values=(k, format_size(e.size)))
        self._update_xp3_sel()

    def _update_xp3_sel(self):
        if not self.entries:
            self.xp3_sel_label.configure(text="")
            return
        self.xp3_sel_label.configure(
            text=f"{len(self.xp3_tree.get_children())} shown · {len(self.xp3_tree.selection())} selected")

    def _on_xp3_select(self):
        self._update_xp3_sel()
        sel = self.xp3_tree.selection()
        if len(sel) != 1:
            return
        e = self.entries[int(sel[0])]
        header = f"{e.name}\n{format_size(e.size)}"
        if e.size > PREVIEW_MAX_SIZE:
            self.xp3_preview.show(header)
            return
        try:
            text, items = entry_preview(self._xp3, self._by_name, e)
        except Exception as ex:
            self.xp3_preview.show(f"{header}\n\nCould not show it:\n{ex}")
            return
        self.xp3_preview.show(f"{header}\n{text}" if text else header, items)

    def start_xp3_extract(self, scope):
        out_dir = self.xp3_out.get()
        if scope != "game" and not self.entries:
            messagebox.showwarning(APP_TITLE, "Open a .xp3 file first.")
            return
        if not out_dir:
            messagebox.showwarning(APP_TITLE, "Choose an output folder.")
            return
        kwargs = {"resume": self.resume_var.get(), "workers": int(self.workers_var.get()),
                  "composed": self.composed_var.get()}
        if scope == "game":
            start = Path(self.xp3_path.get()).parent if self.xp3_path.get() else None
            src = filedialog.askdirectory(title="Game folder", initialdir=start)
            if not src:
                return
            if not find_xp3(src):
                messagebox.showwarning(APP_TITLE, "There are no .xp3 files in that folder.")
                return
            folder = Path(out_dir)
        else:
            src = self.xp3_path.get()
            folder = Path(out_dir) / Path(src).stem
            if scope == "selected":
                names = [self.entries[int(i)].name for i in self.xp3_tree.selection()]
                if not names:
                    messagebox.showwarning(APP_TITLE, "No files selected.")
                    return
                kwargs["names"] = names

        def done(r):
            if scope != "game":
                self._auto(self.xp3_pack_in, folder)
            text = f"{r.files} files ({r.converted} converted to PNG or JSON, {r.composed} composed sprites)"
            if r.skipped:
                text += f" ({r.skipped} were already done)"
            if r.warnings:
                text += f"\n\n{len(r.warnings)} warnings:\n" + "\n".join(r.warnings[:5])
            if r.errors:
                text += f"\n\n{len(r.errors)} errors:\n" + "\n".join(r.errors[:10])
            if r.protected:
                text += f"\n\n{WARNING_TEXT}"
            self._ask_open(f"{text}\n\nin:\n{folder}", folder)

        self.xp3_preview.show()
        self.run_task(krkr_extract.extract_all, (src, out_dir), kwargs,
                      "Extracting the game" if scope == "game" else "Extracting", done)

    def browse_xp3_pack_out(self):
        path = self._ask_save("Save patch", self.xp3_pack_out.get() or "patch.xp3", ".xp3", XP3_TYPES)
        if path:
            self.xp3_pack_out.set(path)

    def start_xp3_pack(self):
        folder, out = self.xp3_pack_in.get(), self.xp3_pack_out.get()
        if not folder or not Path(folder).is_dir():
            messagebox.showwarning(APP_TITLE, "Choose the folder with the files.")
            return
        if not out:
            messagebox.showwarning(APP_TITLE, "Choose the patch file.")
            return
        if Path(out).is_file() and not messagebox.askyesno(
                APP_TITLE, f"{Path(out).name} already exists. Add the files to it (its other files are kept)?"):
            return

        def done(r):
            if r.errors:
                messagebox.showerror(APP_TITLE, "The patch was not created. Errors:\n\n" + "\n".join(r.errors[:15]))
                return
            if not r.entries:
                messagebox.showinfo(APP_TITLE, "No file was edited since the extraction: no file was written.")
                return
            changed = r.changed + r.added
            listing = "\n".join(changed[:15]) + ("\n…" if len(changed) > 15 else "")
            self._ask_open(f"{len(changed)} files in the patch:\n{listing}\n\n{out} ({r.entries} files)\n\n"
                           "Copy it to the game folder.", Path(out).parent)

        self.run_task(krkr_extract.pack_patch, (folder, out), {}, "Packing", done)

    # ------------------------------------------------------------ Pestaña Scripts

    def _build_text_tab(self, tab):
        tab.grid_columnconfigure(1, weight=1)
        tab.grid_rowconfigure(8, weight=1)

        self.tx_game = PathRow(tab, 0, "Game folder:", self.browse_tx_game)
        self.tx_text = PathRow(tab, 1, "Text folder:", lambda: self._browse_dir(self.tx_text, "Text folder"))
        self._busy(self.tx_game.button, self.tx_text.button)

        ext = ctk.CTkFrame(tab)
        ext.grid(row=4, column=0, columnspan=3, sticky="ew", padx=4, pady=(6, 4))
        ctk.CTkLabel(ext, text="Extract", font=ctk.CTkFont(weight="bold")).pack(side="left", padx=8, pady=6)
        self.tx_ref = tk.BooleanVar(value=True)
        self._busy(ctk.CTkCheckBox(ext, text="Reference ('#>')", variable=self.tx_ref)).pack(side="left", padx=8)
        self.tx_json = tk.BooleanVar(value=False)
        self._busy(ctk.CTkCheckBox(ext, text="JSON (with the voice of each line)", variable=self.tx_json)) \
            .pack(side="left", padx=8)
        self._busy(ctk.CTkButton(ext, text="Extract text", width=140, command=self.start_tx_extract)) \
            .pack(side="right", padx=8, pady=6)

        reb = ctk.CTkFrame(tab)
        reb.grid(row=5, column=0, columnspan=3, sticky="ew", padx=4, pady=(0, 4))
        reb.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(reb, text="Rebuild", font=ctk.CTkFont(weight="bold")) \
            .grid(row=0, column=0, sticky="w", padx=8, pady=(6, 0))
        self.tx_out = PathRow(reb, 1, "Patch .xp3:", self.browse_tx_out, "Save as…")
        self._busy(self.tx_out.button)
        muted(ctk.CTkLabel, reb, anchor="w",
              text="Writes scene.sdb and scenepatch.sdb into the patch. Lines can be any length.") \
            .grid(row=2, column=1, sticky="w")
        self._busy(ctk.CTkButton(reb, text="Rebuild", width=140, command=self.start_tx_rebuild)) \
            .grid(row=2, column=2, sticky="e", padx=4, pady=(3, 8))
        muted(ctk.CTkLabel, reb, justify="left", anchor="w",
              text="Reference: the text of the game without its text patch (e.g. the Japanese under an English "
                   "patch) is added\nas a '#>' comment. Translate only the text after '|'.") \
            .grid(row=3, column=0, columnspan=3, sticky="ew", padx=8, pady=(0, 6))

        bar = ctk.CTkFrame(tab, fg_color="transparent")
        bar.grid(row=6, column=0, columnspan=3, sticky="ew", padx=4)
        self.report_label = muted(ctk.CTkLabel, bar, text="The report will appear here after rebuilding.",
                                  anchor="w", justify="left", wraplength=640)
        self.report_label.pack(side="left", fill="x", expand=True)
        self.report_filter = ctk.CTkOptionMenu(bar, values=list(REPORT_FILTERS), width=200,
                                               command=lambda _v: self._refresh_report())
        self.report_filter.pack(side="right")

        frame, self.report_tree = self._make_tree(
            tab, ("entry", "key", "state", "detail"), ("File", "Line", "Status", "Detail"), (160, 90, 120, 0))
        frame.grid(row=8, column=0, columnspan=3, sticky="nsew", padx=4, pady=4)

    def browse_tx_game(self):
        path = filedialog.askdirectory(title="Game folder")
        if path:
            self.tx_game.set(path)
            game = Path(path)
            self._auto(self.tx_text, game.parent / f"{game.name}_text")
            self._auto(self.tx_out, self._patch_path(game))

    def browse_tx_out(self):
        path = self._ask_save("Save patch", self.tx_out.get() or "patch.xp3", ".xp3", XP3_TYPES)
        if path:
            self.tx_out.set(path)

    def _check_game(self, game) -> bool:
        if not game or not Path(game).is_dir() or not find_xp3(game):
            messagebox.showwarning(APP_TITLE, "Choose the game folder (with the .xp3 files).")
            return False
        return True

    def start_tx_extract(self):
        game, folder = self.tx_game.get(), self.tx_text.get()
        if not self._check_game(game):
            return
        if not folder:
            messagebox.showwarning(APP_TITLE, "Choose the text folder.")
            return

        def done(r):
            text = (f"{r.lines} lines from {r.files} scripts. One file per script: KEY|TEXT; translate only "
                    "the text after '|'.")
            if r.reference:
                text += f" Reference: the text without {r.reference}."
            if r.warnings:
                text += "\n" + " · ".join(r.warnings[:3])
            self.report_label.configure(text=text)
            self._ask_open(f"Extracted {r.lines} lines from {r.files} scripts to:\n{folder}", folder)

        self.run_task(krkr_script.extract_text, (game, folder),
                      {"reference": self.tx_ref.get(), "with_json": self.tx_json.get()},
                      "Extracting text", done)

    def start_tx_rebuild(self):
        game, folder, out = self.tx_game.get(), self.tx_text.get(), self.tx_out.get()
        if not self._check_game(game):
            return
        if not folder or not Path(folder).is_dir():
            messagebox.showwarning(APP_TITLE, "Choose the translated text folder.")
            return
        if not out:
            messagebox.showwarning(APP_TITLE, "Choose the patch file.")
            return
        if Path(out).resolve().parent == Path(game).resolve():
            messagebox.showwarning(APP_TITLE, "Save the patch outside the game folder, then copy it there.")
            return

        def done(rep):
            self.report = rep
            text = rep.summary()
            if rep.warnings:
                text += "\n" + " · ".join(rep.warnings[:3])
            self.report_label.configure(text=text)
            self._refresh_report()
            msg = rep.summary()
            if rep.output_entries:
                msg = f"Patch created ({rep.output_entries} files):\n{out}\n\n{msg}\n\nCopy it to the game folder."
            self._ask_open(msg, Path(out).parent)

        self.run_task(krkr_script.rebuild, (game, folder, out), {}, "Rebuilding", done)

    def _refresh_report(self):
        self.report_tree.delete(*self.report_tree.get_children())
        if not self.report:
            return
        keep = REPORT_FILTERS[self.report_filter.get()]
        rows = [e for e in self.report.entries if keep(e)]
        for i, e in enumerate(rows[:MAX_REPORT_ROWS]):
            self.report_tree.insert("", "end", iid=str(i), text=e.entry,
                                    values=(e.key, ", ".join(e.flags), e.detail))
        if len(rows) > MAX_REPORT_ROWS:
            self.report_tree.insert("", "end", iid="more", text="…",
                                    values=("", f"{len(rows) - MAX_REPORT_ROWS} more", ""))

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
        ready = "Ready" if native_available() else "Ready (_krkr.dll not found: TLG images use the much slower Python version)"
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
        style.configure("Krkr.Treeview", background=bg, fieldbackground=bg, foreground=fg,
                        rowheight=24, borderwidth=0)
        style.map("Krkr.Treeview", background=[("selected", "#1f6aa5")], foreground=[("selected", "#ffffff")])
        style.configure("Krkr.Treeview.Heading", background=head_bg, foreground=fg, relief="flat")
        style.map("Krkr.Treeview.Heading", background=[("active", head_bg)])

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
        self.xp3_preview.show_file(name)

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
