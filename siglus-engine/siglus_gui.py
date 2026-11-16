#!/usr/bin/env python3
"""
Siglus Tools GUI - Recursos (G00, NWA, OVK, OMV) y guiones (Scene.pck) de SiglusEngine
Probado con Hoshi Ori Yume Mirai Perfect Edition (Tone Work's, 2017).
"""

import multiprocessing
import os
import queue
import threading
import time
import tkinter as tk
from collections import Counter
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk
from PIL import Image

import siglus_extract
import siglus_g00
import siglus_script
from siglus_audio import is_nwa, nwa_info, ogg_seconds, omv_ogg_offset, read_ovk_index
from siglus_common import Cancelled
from siglus_crypto import native_available
from siglus_gameexe import dbs_to_csv, read_gameexe
from siglus_pck import ScenePck

APP_TITLE = "Siglus Tools"
PREVIEW_SIZE = (300, 260)
PREVIEW_INTERVAL = 0.5      # segundos entre actualizaciones de la vista previa al extraer
PREVIEW_MAX_SIZE = 64 << 20
MAX_REPORT_ROWS = 5000
ALL_TYPES = "All"
PCK_TYPES = [("Scene.pck", "Scene.pck"), ("PCK files", "*.pck"), ("All", "*.*")]

REPORT_FILTERS = {
    "All": lambda e: True,
    "Changed": lambda e: siglus_script.ST_CHANGED in e.flags,
    "Not text lines": lambda e: siglus_script.ST_NOT_TEXT in e.flags,
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


def _lines(text: str, n: int = 10) -> str:
    return "\n".join([line[:60] for line in text.splitlines() if line.strip()][:n])


def face_preview(game: Path, rel: str, face: str, data: bytes):
    """Una CG o sprite sin cara con cada expresión de su archivo _FACE (cargadas al mostrarlas)."""
    face_data = (game / face).read_bytes()
    if siglus_g00.g00_info(face_data)[1:3] != siglus_g00.g00_info(data)[1:3]:
        return None
    count = len(siglus_g00.region_indexes(face_data))
    cache = {}

    def layer(i):
        if 'layers' not in cache:
            cache['layers'] = siglus_g00.layers(face_data)
        return cache['layers'][i]

    items = [((lambda i=i: siglus_g00.with_face(data, layer(i))), f"With face {i + 1} of {count} ({Path(face).name})")
             for i in range(count)]
    items.append(((lambda: siglus_g00.compose(data)), "Without face"))
    first = Path(siglus_extract.composed_path(rel)).name
    return (f"Image without face: shown with each face of {Path(face).name}\n"
            f"Also saved complete (face 1) as _composed/{first}"), items


def file_preview(game: Path, rel: str, face: str = None):
    """(texto, [(imagen o función, texto)]) para la vista previa de un archivo del juego."""
    path = game / rel
    low = rel.lower()
    if low.endswith(('.g00', '.g01')):
        data = path.read_bytes()
        if face:
            result = face_preview(game, rel, face, data)
            if result:
                return result
        t, w, h, regions = siglus_g00.g00_info(data)
        outputs = siglus_extract.g00_outputs(rel, data)
        if outputs[0][1] is None:
            desc = f"G00 type {t}, {w}×{h}" + (f", {regions} regions" if t == 2 else "")
            return "", [((lambda: siglus_g00.compose(data)), f"{desc}\nSaved as {Path(outputs[0][0]).name}")]
        items = []
        for (out, _r), (img, d) in zip(outputs, siglus_g00.decode(data)):
            items.append((img, f"G00 type 2, {d}\nSaved as {Path(out).name}"))
        return f"{len(items)} images (regions that overlap)", items
    if low.endswith('.nwa'):
        data = path.read_bytes()
        if is_nwa(data):
            ch, bps, rate, secs = nwa_info(data)
            return f"Audio (NWA): {secs:.1f} s, {rate} Hz, {ch} ch, {bps}-bit\nSaved as {Path(rel).stem}.wav", []
    if low.endswith('.ogg'):
        return f"Audio (Ogg Vorbis): {ogg_seconds(path.read_bytes()):.1f} s", []
    if low.endswith('.ovk'):
        with open(path, 'rb') as f:
            head = f.read(4 + 16 * 4096)
        entries = read_ovk_index(head)
        return (f"Voices: {len(entries)} Ogg files\nSaved to koe/{path.stem}/{path.stem}_NNNNN.ogg", [])
    if low.endswith('.omv'):
        return f"Video (OMV, Ogg Theora from byte {omv_ogg_offset(path):,})\nSaved as {path.stem}.ogv", []
    if low.endswith('.dbs'):
        return "Table (also saved as .csv)\n\n" + _lines(dbs_to_csv(path.read_bytes())), []
    if low == 'gameexe.dat':
        return "Configuration (saved as Gameexe.txt)\n\n" + _lines(read_gameexe(path.read_bytes())), []
    return siglus_extract.kind_of(rel).capitalize(), []


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

        self.game = None            # carpeta del juego abierta
        self.files = []             # rutas relativas de sus recursos
        self._by_lower = {}
        self.sizes = []
        self.kinds = []
        self.report = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.tabs = ctk.CTkTabview(self)
        self.tabs.grid(row=0, column=0, sticky="nsew", padx=12, pady=(6, 0))
        self._build_files_tab(self.tabs.add("Game Files"))
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
        tree = ttk.Treeview(frame, columns=columns[1:], selectmode="extended", style="Siglus.Treeview")
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
    def _sibling(game, suffix) -> Path:
        """<carpeta del juego><suffix>, junto al juego."""
        game = Path(game)
        return game.parent / f"{game.name}{suffix}"

    # ------------------------------------------------------------ Pestaña Game Files

    def _build_files_tab(self, tab):
        tab.grid_columnconfigure(1, weight=1)
        tab.grid_rowconfigure(3, weight=1)

        self.game_path = PathRow(tab, 0, "Game folder:", self.browse_game, "Open…")
        self.game_path.entry.bind("<Return>", lambda _e: self.load_game(self.game_path.get()))
        self._busy(self.game_path.button)

        self.files_info = muted(ctk.CTkLabel, tab, text="No game open", anchor="w", justify="left")
        self.files_info.grid(row=1, column=0, columnspan=3, sticky="ew", padx=4)

        self.files_filter = tk.StringVar()
        self.files_filter.trace_add("write", lambda *_: self._refresh_files_tree())
        ctk.CTkLabel(tab, text="Filter:").grid(row=2, column=0, sticky="w", padx=(4, 8), pady=3)
        filt = ctk.CTkFrame(tab, fg_color="transparent")
        filt.grid(row=2, column=1, columnspan=2, sticky="ew", padx=(0, 4), pady=3)
        filt.grid_columnconfigure(0, weight=1)
        ctk.CTkEntry(filt, textvariable=self.files_filter, placeholder_text="name contains…") \
            .grid(row=0, column=0, sticky="ew")
        self.files_type = tk.StringVar(value=ALL_TYPES)
        self.files_type_menu = ctk.CTkOptionMenu(filt, values=[ALL_TYPES], width=100, variable=self.files_type,
                                                 command=lambda _v: self._refresh_files_tree())
        self.files_type_menu.grid(row=0, column=1, padx=(8, 0))

        body = ctk.CTkFrame(tab, fg_color="transparent")
        body.grid(row=3, column=0, columnspan=3, sticky="nsew", padx=4, pady=4)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)
        frame, self.files_tree = self._make_tree(body, ("name", "type", "size"), ("Name", "Type", "Size"),
                                                 (300, 80, 100), {"size": "e"})
        frame.grid(row=0, column=0, sticky="nsew")
        self.files_tree.bind("<<TreeviewSelect>>", lambda _e: self._on_files_select())
        self.files_preview = Preview(body)
        self.files_preview.grid(row=0, column=1, sticky="ns", padx=(8, 0))
        self.files_preview.bind_keys(self.files_tree)

        self.files_out = PathRow(tab, 4, "Output:", lambda: self._browse_dir(self.files_out, "Output folder"))
        self._busy(self.files_out.button)

        actions = ctk.CTkFrame(tab, fg_color="transparent")
        actions.grid(row=5, column=0, columnspan=3, sticky="ew", padx=4, pady=(2, 0))
        self.resume_var = tk.BooleanVar(value=True)
        self._busy(ctk.CTkCheckBox(actions, text="Resume", variable=self.resume_var)).pack(side="left")
        self.composed_var = tk.BooleanVar(value=True)
        self._busy(ctk.CTkCheckBox(actions, text="Compose CGs", variable=self.composed_var)) \
            .pack(side="left", padx=(12, 0))
        ctk.CTkLabel(actions, text="Workers:").pack(side="left", padx=(12, 6))
        self.workers_var = tk.StringVar(value=str(siglus_extract.default_workers()))
        self._busy(ctk.CTkOptionMenu(actions, values=[str(n) for n in range(1, (os.cpu_count() or 1) + 1)],
                                     width=70, variable=self.workers_var)).pack(side="left")
        self.files_sel_label = muted(ctk.CTkLabel, actions, text="")
        self.files_sel_label.pack(side="left", padx=12)
        self._busy(ctk.CTkButton(actions, text="Extract all", width=110,
                                 command=lambda: self.start_files_extract(False))).pack(side="right")
        self._busy(ctk.CTkButton(actions, text="Extract selected", width=120,
                                 command=lambda: self.start_files_extract(True))).pack(side="right", padx=8)
        muted(ctk.CTkLabel, tab, anchor="w",
              text="Images are saved as PNG, music and sound effects as .wav, voices as .ogg, videos as .ogv, "
                   "with the folders of the game.\nCompose CGs: CGs and sprites without a face are also saved "
                   "complete, with their first face, in g00/_composed.") \
            .grid(row=6, column=0, columnspan=3, sticky="ew", padx=4, pady=(2, 0))

        pack = self._section(tab, 7, "Pack")
        self.pack_in = PathRow(pack, 1, "Extracted:", lambda: self._browse_dir(self.pack_in, "Extracted folder"))
        self.pack_out = PathRow(pack, 2, "Output:", lambda: self._browse_dir(self.pack_out, "Output folder"))
        self._busy(self.pack_in.button, self.pack_out.button)
        self._busy(ctk.CTkButton(pack, text="Pack", width=140, command=self.start_pack)) \
            .grid(row=3, column=2, sticky="e", padx=4, pady=(2, 8))
        muted(ctk.CTkLabel, pack, anchor="w", justify="left",
              text="Writes the images edited since the extraction as .g00 (they must keep their size) and "
                   "Gameexe.txt as Gameexe.dat,\nwith the folders of the game: copy them over the game folder.") \
            .grid(row=4, column=0, columnspan=3, sticky="ew", padx=8, pady=(0, 6))

    def browse_game(self):
        path = filedialog.askdirectory(title="Game folder (with Scene.pck and g00)")
        if path:
            self.load_game(path)

    def load_game(self, path):
        path = path.strip().strip('"')
        if not path:
            return
        game = Path(path)
        files = siglus_extract.find_files(game) if game.is_dir() else []
        if not files:
            messagebox.showwarning(APP_TITLE, "There are no SiglusEngine resources in that folder.")
            return
        self.game = game
        self.files = files
        self._by_lower = {f.lower(): f for f in files}
        self.sizes = [(game / f).stat().st_size for f in files]
        self.kinds = [siglus_extract.kind_of(f) for f in files]
        self.game_path.set(str(game))
        self._auto(self.files_out, self._sibling(game, "_extracted"))
        self._auto(self.pack_in, self.files_out.get())
        self._auto(self.pack_out, self._sibling(game, "_new"))
        if (game / siglus_script.PCK_NAME).is_file():
            self._auto(self.tx_pck, game / siglus_script.PCK_NAME)
            self._auto(self.tx_text, self._sibling(game, "_text"))
            self._auto(self.tx_out, self._sibling(game, "_new") / siglus_script.PCK_NAME)

        types = Counter(self.kinds)
        text = (f"{len(files)} files  ·  {format_size(sum(self.sizes))}  ·  "
                + ", ".join(f"{n} {k}" for k, n in types.most_common()))
        try:
            pck = ScenePck(game / siglus_script.PCK_NAME)
            text += f"\nScene.pck: {len(pck)} scenes" + (f", key {pck.key.hex(' ').upper()}" if pck.key else "")
        except (OSError, ValueError) as e:
            text += f"\nScene.pck: {e}"
        self.files_info.configure(text=text)
        self.files_type_menu.configure(values=[ALL_TYPES] + sorted(types))
        self.files_type.set(ALL_TYPES)
        self._refresh_files_tree()
        self.files_preview.show()

    def _refresh_files_tree(self):
        self.files_tree.delete(*self.files_tree.get_children())
        text = self.files_filter.get().lower()
        kind = self.files_type.get()
        for i, (f, k, s) in enumerate(zip(self.files, self.kinds, self.sizes)):
            if text in f.lower() and kind in (ALL_TYPES, k):
                self.files_tree.insert("", "end", iid=str(i), text=f, values=(k, format_size(s)))
        self._update_files_sel()

    def _update_files_sel(self):
        if not self.files:
            self.files_sel_label.configure(text="")
            return
        self.files_sel_label.configure(
            text=f"{len(self.files_tree.get_children())} shown · {len(self.files_tree.selection())} selected")

    def _on_files_select(self):
        self._update_files_sel()
        sel = self.files_tree.selection()
        if len(sel) != 1:
            return
        i = int(sel[0])
        rel = self.files[i]
        header = f"{rel}\n{format_size(self.sizes[i])}"
        if self.sizes[i] > PREVIEW_MAX_SIZE and not rel.lower().endswith(('.omv', '.ovk')):
            self.files_preview.show(header)
            return
        try:
            text, items = file_preview(self.game, rel, siglus_extract.face_partner(rel, self._by_lower))
        except Exception as ex:
            self.files_preview.show(f"{header}\n\nCould not show it:\n{ex}")
            return
        self.files_preview.show(f"{header}\n{text}" if text else header, items)

    def start_files_extract(self, selected):
        out_dir = self.files_out.get()
        if not self.files:
            messagebox.showwarning(APP_TITLE, "Open the game folder first.")
            return
        if not out_dir:
            messagebox.showwarning(APP_TITLE, "Choose an output folder.")
            return
        kwargs = {"resume": self.resume_var.get(), "workers": int(self.workers_var.get()),
                  "composed": self.composed_var.get()}
        if selected:
            names = [self.files[int(i)] for i in self.files_tree.selection()]
            if not names:
                messagebox.showwarning(APP_TITLE, "No files selected.")
                return
            kwargs["names"] = names

        def done(r):
            self._auto(self.pack_in, out_dir)
            text = f"{r.files} files ({r.converted} converted, {r.written} written)"
            if r.skipped:
                text += f" ({r.skipped} were already done)"
            if r.errors:
                text += f"\n\n{len(r.errors)} errors:\n" + "\n".join(r.errors[:10])
            self._ask_open(f"{text}\n\nin:\n{out_dir}", Path(out_dir))

        self.files_preview.show()
        self.run_task(siglus_extract.extract_all, (str(self.game), out_dir), kwargs, "Extracting", done)

    def start_pack(self):
        folder, out = self.pack_in.get(), self.pack_out.get()
        if not self.game:
            messagebox.showwarning(APP_TITLE, "Open the game folder first.")
            return
        if not folder or not Path(folder).is_dir():
            messagebox.showwarning(APP_TITLE, "Choose the extracted folder.")
            return
        if not out:
            messagebox.showwarning(APP_TITLE, "Choose an output folder.")
            return

        def done(r):
            if not r.written and not r.errors:
                messagebox.showinfo(APP_TITLE, "No file was edited since the extraction: no file was written.")
                return
            text = f"{len(r.written)} files written:\n" + "\n".join(r.written[:15]) + \
                ("\n…" if len(r.written) > 15 else "")
            if r.warnings:
                text += f"\n\n{len(r.warnings)} warnings:\n" + "\n".join(r.warnings[:5])
            if r.errors:
                text += f"\n\n{len(r.errors)} errors:\n" + "\n".join(r.errors[:10])
            self._ask_open(f"{text}\n\nin:\n{out}\n\nCopy them over the game folder.", Path(out))

        self.run_task(siglus_extract.pack, (folder, str(self.game), out), {}, "Packing", done)

    # ------------------------------------------------------------ Pestaña Scripts

    def _build_text_tab(self, tab):
        tab.grid_columnconfigure(1, weight=1)
        tab.grid_rowconfigure(8, weight=1)

        self.tx_pck = PathRow(tab, 0, "Scene.pck:", self.browse_tx_pck, "Open…")
        self.tx_ref = PathRow(tab, 1, "Reference:", self.browse_tx_ref, "Open…")
        self.tx_text = PathRow(tab, 2, "Text folder:", lambda: self._browse_dir(self.tx_text, "Text folder"))
        self._busy(self.tx_pck.button, self.tx_ref.button, self.tx_text.button)

        ext = ctk.CTkFrame(tab)
        ext.grid(row=4, column=0, columnspan=3, sticky="ew", padx=4, pady=(6, 4))
        ctk.CTkLabel(ext, text="Extract", font=ctk.CTkFont(weight="bold")).pack(side="left", padx=8, pady=6)
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
        self.tx_out = PathRow(reb, 1, "Output .pck:", self.browse_tx_out, "Save as…")
        self._busy(self.tx_out.button)
        muted(ctk.CTkLabel, reb, anchor="w",
              text="Lines can be any length. Scenes without changes are copied unchanged.") \
            .grid(row=2, column=1, sticky="w")
        self._busy(ctk.CTkButton(reb, text="Rebuild", width=140, command=self.start_tx_rebuild)) \
            .grid(row=2, column=2, sticky="e", padx=4, pady=(3, 8))
        muted(ctk.CTkLabel, reb, justify="left", anchor="w",
              text="Reference (optional): the same Scene.pck in another language (for example the original "
                   "Japanese one). Its text\nis added to each line as a '#>' comment. Keep the codes ％Ｂ, #N "
                   "and 【name】.") \
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
            tab, ("entry", "key", "state", "detail"), ("Scene", "Line", "Status", "Detail"), (160, 80, 120, 0))
        frame.grid(row=8, column=0, columnspan=3, sticky="nsew", padx=4, pady=4)

    def browse_tx_pck(self):
        path = filedialog.askopenfilename(title="Scene.pck", filetypes=PCK_TYPES)
        if path:
            self.tx_pck.set(path)
            game = Path(path).parent
            self._auto(self.tx_text, self._sibling(game, "_text"))
            self._auto(self.tx_out, self._sibling(game, "_new") / siglus_script.PCK_NAME)

    def browse_tx_ref(self):
        path = filedialog.askopenfilename(title="Reference Scene.pck (optional)", filetypes=PCK_TYPES)
        if path:
            self.tx_ref.set(path)

    def browse_tx_out(self):
        path = self._ask_save("Save Scene.pck", self.tx_out.get() or "Scene.pck", ".pck", PCK_TYPES)
        if path:
            self.tx_out.set(path)

    def start_tx_extract(self):
        pck, ref, folder = self.tx_pck.get(), self.tx_ref.get(), self.tx_text.get()
        if not pck or not Path(pck).is_file():
            messagebox.showwarning(APP_TITLE, "Choose the Scene.pck.")
            return
        if ref and not Path(ref).is_file():
            messagebox.showwarning(APP_TITLE, "The reference does not exist.")
            return
        if not folder:
            messagebox.showwarning(APP_TITLE, "Choose the text folder.")
            return

        def done(r):
            text = (f"{r.lines} lines ({r.voiced} with voice) from {r.files} scenes. One file per scene: "
                    "NUMBER|TEXT; translate only the text after '|'.")
            if r.warnings:
                text += "\n" + " · ".join(r.warnings[:3])
            self.report_label.configure(text=text)
            self._ask_open(f"Extracted {r.lines} lines from {r.files} scenes to:\n{folder}", folder)

        self.run_task(siglus_script.extract_text, (pck, folder),
                      {"reference": ref or None, "with_json": self.tx_json.get()}, "Extracting text", done)

    def start_tx_rebuild(self):
        pck, folder, out = self.tx_pck.get(), self.tx_text.get(), self.tx_out.get()
        if not pck or not Path(pck).is_file():
            messagebox.showwarning(APP_TITLE, "Choose the Scene.pck.")
            return
        if not folder or not Path(folder).is_dir():
            messagebox.showwarning(APP_TITLE, "Choose the translated text folder.")
            return
        if not self._check_output(out, source=pck):
            return

        def done(rep):
            self.report = rep
            text = rep.summary()
            if rep.warnings:
                text += "\n" + " · ".join(rep.warnings[:3])
            self.report_label.configure(text=text)
            self._refresh_report()
            msg = rep.summary()
            if rep.written:
                msg = f"Scene.pck created:\n{out}\n\n{msg}\n\nCopy it over the one in the game folder."
            self._ask_open(msg, Path(out).parent)

        self.run_task(siglus_script.rebuild, (pck, folder, out), {}, "Rebuilding", done)

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
        ready = "Ready" if native_available() else "Ready (_siglus.dll not found: the much slower Python version is used)"
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
        style.configure("Siglus.Treeview", background=bg, fieldbackground=bg, foreground=fg,
                        rowheight=24, borderwidth=0)
        style.map("Siglus.Treeview", background=[("selected", "#1f6aa5")], foreground=[("selected", "#ffffff")])
        style.configure("Siglus.Treeview.Heading", background=head_bg, foreground=fg, relief="flat")
        style.map("Siglus.Treeview.Heading", background=[("active", head_bg)])

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
        self.files_preview.show_file(name)

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
