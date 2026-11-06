#!/usr/bin/env python3
"""
AdvHD Tools GUI - Archivos .arc, imágenes WIPF y guiones (motor AdvHD de WillPlus)
Juego: CROSS†CHANNEL (edición WillPlus, 2012)
"""

import io
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

import advhd_extract
import advhd_wsc
from advhd_arc import pack, read_entry, read_index
from advhd_common import Cancelled
from advhd_wipf import frame_to_image, mask_to_alpha, read_wipf

APP_TITLE = "AdvHD Tools"
PREVIEW_SIZE = (300, 260)
PREVIEW_INTERVAL = 0.5      # segundos entre actualizaciones de la vista previa al extraer
PREVIEW_MAX_SIZE = 64 << 20
MAX_REPORT_ROWS = 5000
ALL_TYPES = "All"

MODE_RELOC = "Relocate (no limit)"
MODE_FIXED = "Fixed size"

REPORT_FILTERS = {
    "With warnings": lambda e: advhd_wsc.ST_CUT in e.flags or advhd_wsc.ST_UNSUPPORTED in e.flags,
    "Grew": lambda e: advhd_wsc.ST_GREW in e.flags,
    "Cut": lambda e: advhd_wsc.ST_CUT in e.flags,
    "Replaced characters": lambda e: advhd_wsc.ST_REPLACED in e.flags,
    "Unsupported characters": lambda e: advhd_wsc.ST_UNSUPPORTED in e.flags,
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


def image_items(f, parts: dict):
    """
    Imágenes de un nombre del .arc como en la extracción: PNG o WIP (con la máscara MSK como
    alfa si coincide), la máscara sola y los MOS. parts: {tipo: ArcEntry}. Devuelve [(imagen, texto)].
    """
    name = next(iter(parts.values())).name

    def frames(ext):
        return read_wipf(read_entry(f, parts[ext])) if ext in parts else []

    masks = [mask_to_alpha(fr) for fr in frames('MSK')]
    if all(m.getextrema() == (0, 0) for m in masks):
        masks_alpha = []        # totalmente transparente: la extracción no la aplica
    else:
        masks_alpha = masks
    items = []

    def add(images, suffix, note=""):
        names = advhd_extract._frame_names(name, len(images), suffix)
        for fname, img in zip(names, images):
            items.append((img, f"{img.width}×{img.height}{note}\nSaved as {fname}"))

    if 'PNG' in parts:
        img = Image.open(io.BytesIO(read_entry(f, parts['PNG'])))
        if masks_alpha and img.size == masks_alpha[0].size:
            img = img.convert('RGB')
            img.putalpha(masks_alpha[0])
            add([img], '', " + mask")
        else:
            add([img], '')
            add(masks, '_msk')
    elif 'WIP' in parts:
        images = [frame_to_image(fr) for fr in frames('WIP')]
        if masks_alpha and [m.size for m in masks_alpha] == [im.size for im in images]:
            composed = []
            for img, m in zip(images, masks_alpha):
                img = img.convert('RGB')
                img.putalpha(m)
                composed.append(img)
            add(composed, '', " + mask")
        else:
            add(images, '')
            add(masks, '_msk')
    else:
        add(masks, '_msk')
    add([frame_to_image(fr) for fr in frames('MOS')], '_mos')
    return items


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

        self.entries = []           # pestaña ARC Files: lista de advhd_arc.ArcEntry
        self.report = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.tabs = ctk.CTkTabview(self)
        self.tabs.grid(row=0, column=0, sticky="nsew", padx=12, pady=(6, 0))
        self._build_arc_tab(self.tabs.add("ARC Files"))
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
        tree = ttk.Treeview(frame, columns=columns[1:], selectmode="extended", style="AdvHD.Treeview")
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

    # ------------------------------------------------------------ Pestaña ARC

    def _build_arc_tab(self, tab):
        tab.grid_columnconfigure(1, weight=1)
        tab.grid_rowconfigure(3, weight=1)

        self.arc_path = PathRow(tab, 0, ".arc file:", self.browse_arc, "Open…")
        self.arc_path.entry.bind("<Return>", lambda _e: self.load_arc(self.arc_path.get()))
        self._busy(self.arc_path.button)

        self.arc_info = muted(ctk.CTkLabel, tab, text="No file open", anchor="w")
        self.arc_info.grid(row=1, column=0, columnspan=3, sticky="ew", padx=4)

        self.arc_filter = tk.StringVar()
        self.arc_filter.trace_add("write", lambda *_: self._refresh_arc_tree())
        ctk.CTkLabel(tab, text="Filter:").grid(row=2, column=0, sticky="w", padx=(4, 8), pady=3)
        filt = ctk.CTkFrame(tab, fg_color="transparent")
        filt.grid(row=2, column=1, columnspan=2, sticky="ew", padx=(0, 4), pady=3)
        filt.grid_columnconfigure(0, weight=1)
        ctk.CTkEntry(filt, textvariable=self.arc_filter, placeholder_text="name contains…") \
            .grid(row=0, column=0, sticky="ew")
        self.arc_type = tk.StringVar(value=ALL_TYPES)
        self.arc_type_menu = ctk.CTkOptionMenu(filt, values=[ALL_TYPES], width=100, variable=self.arc_type,
                                               command=lambda _v: self._refresh_arc_tree())
        self.arc_type_menu.grid(row=0, column=1, padx=(8, 0))

        body = ctk.CTkFrame(tab, fg_color="transparent")
        body.grid(row=3, column=0, columnspan=3, sticky="nsew", padx=4, pady=4)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)
        frame, self.arc_tree = self._make_tree(body, ("name", "type", "size"), ("Name", "Type", "Size"),
                                               (300, 70, 100), {"size": "e"})
        frame.grid(row=0, column=0, sticky="nsew")
        self.arc_tree.bind("<<TreeviewSelect>>", lambda _e: self._on_arc_select())
        self.arc_preview = Preview(body)
        self.arc_preview.grid(row=0, column=1, sticky="ns", padx=(8, 0))
        self.arc_preview.bind_keys(self.arc_tree)

        self.arc_out = PathRow(tab, 4, "Output:", lambda: self._browse_dir(self.arc_out, "Output folder"))
        self._busy(self.arc_out.button)

        actions = ctk.CTkFrame(tab, fg_color="transparent")
        actions.grid(row=5, column=0, columnspan=3, sticky="ew", padx=4, pady=(2, 0))
        self.resume_var = tk.BooleanVar(value=True)
        self._busy(ctk.CTkCheckBox(actions, text="Resume", variable=self.resume_var)).pack(side="left")
        ctk.CTkLabel(actions, text="Workers:").pack(side="left", padx=(12, 6))
        self.workers_var = tk.StringVar(value=str(advhd_extract.default_workers()))
        self._busy(ctk.CTkOptionMenu(actions, values=[str(n) for n in range(1, (os.cpu_count() or 1) + 1)],
                                     width=70, variable=self.workers_var)).pack(side="left")
        self.arc_sel_label = muted(ctk.CTkLabel, actions, text="")
        self.arc_sel_label.pack(side="left", padx=12)
        self._busy(ctk.CTkButton(actions, text="Extract game…", width=120,
                                 command=lambda: self.start_arc_extract("game"))).pack(side="right")
        self._busy(ctk.CTkButton(actions, text="Extract all", width=110,
                                 command=lambda: self.start_arc_extract("all"))).pack(side="right", padx=8)
        self._busy(ctk.CTkButton(actions, text="Extract selected", width=120,
                                 command=lambda: self.start_arc_extract("selected"))).pack(side="right")
        muted(ctk.CTkLabel, tab, anchor="w",
              text="Images are saved as PNG (with the MSK mask as transparency) and the rest as is, "
                   "in a folder per .arc.") \
            .grid(row=6, column=0, columnspan=3, sticky="ew", padx=4, pady=(2, 0))

        pack_box = self._section(tab, 7, "Pack")
        self.arc_pack_in = PathRow(pack_box, 1, "Folder:", lambda: self._browse_dir(self.arc_pack_in,
                                                                                     "Folder with the files"))
        self.arc_pack_out = PathRow(pack_box, 2, ".arc file:", self.browse_arc_pack_out, "Save as…")
        self._busy(self.arc_pack_in.button, self.arc_pack_out.button)
        self.resize_var = tk.BooleanVar(value=False)
        self._busy(ctk.CTkCheckBox(pack_box, text="Allow images with a different size than the original",
                                   variable=self.resize_var)).grid(row=3, column=1, sticky="w")
        self._busy(ctk.CTkButton(pack_box, text="Pack", width=140, command=self.start_arc_pack)) \
            .grid(row=3, column=2, sticky="e", padx=4, pady=(2, 8))
        muted(ctk.CTkLabel, pack_box, anchor="w", justify="left",
              text="An extracted folder is repacked over the open .arc (only the edited images are "
                   "re-encoded);\nany other folder of NAME.ext files becomes a new .arc.") \
            .grid(row=4, column=0, columnspan=3, sticky="ew", padx=8, pady=(0, 6))

    def browse_arc(self):
        path = filedialog.askopenfilename(title="Open .arc file",
                                          filetypes=[("ARC files", "*.arc"), ("All", "*.*")])
        if path:
            self.load_arc(path)

    @staticmethod
    def _new_path(arc_path) -> Path:
        """<carpeta del juego>_new/<nombre>.arc, junto al juego."""
        p = Path(arc_path)
        return p.parent.parent / f"{p.parent.name}_new" / p.name

    def load_arc(self, path):
        path = path.strip().strip('"')
        if not path:
            return
        try:
            entries = read_index(path)
        except Exception as e:
            messagebox.showerror(APP_TITLE, f"Could not read the file:\n{e}")
            return
        self.entries = entries
        self.arc_path.set(path)
        p = Path(path)
        self._auto(self.arc_out, p.parent.parent / f"{p.parent.name}_extracted")
        self._auto(self.arc_pack_in, Path(self.arc_out.get()) / p.stem)
        self._auto(self.arc_pack_out, self._new_path(path))

        types = Counter(e.ext for e in entries)
        total = sum(e.size for e in entries)
        self.arc_info.configure(text=f"{len(entries)} files  ·  {format_size(total)}  ·  "
                                     + ", ".join(f"{n} {ext}" for ext, n in types.most_common()))
        self.arc_type_menu.configure(values=[ALL_TYPES] + sorted(types))
        self.arc_type.set(ALL_TYPES)
        self._refresh_arc_tree()
        self.arc_preview.show()

    def _refresh_arc_tree(self):
        self.arc_tree.delete(*self.arc_tree.get_children())
        text = self.arc_filter.get().lower()
        ext = self.arc_type.get()
        for i, e in enumerate(self.entries):
            if text in e.filename.lower() and ext in (ALL_TYPES, e.ext):
                self.arc_tree.insert("", "end", iid=str(i), text=e.name, values=(e.ext, format_size(e.size)))
        self._update_arc_sel()

    def _update_arc_sel(self):
        if not self.entries:
            self.arc_sel_label.configure(text="")
            return
        self.arc_sel_label.configure(
            text=f"{len(self.arc_tree.get_children())} shown · {len(self.arc_tree.selection())} selected")

    def _on_arc_select(self):
        self._update_arc_sel()
        sel = self.arc_tree.selection()
        if len(sel) != 1:
            return
        e = self.entries[int(sel[0])]
        header = f"{e.filename}\n{format_size(e.size)}"
        if e.ext.upper() not in advhd_extract.IMAGE_TYPES or e.size > PREVIEW_MAX_SIZE:
            self.arc_preview.show(header)
            return
        parts = {x.ext.upper(): x for x in self.entries if x.name == e.name}
        try:
            with open(self.arc_path.get(), 'rb') as f:
                items = image_items(f, parts)
        except Exception as ex:
            self.arc_preview.show(f"{header}\n\nCould not show it:\n{ex}")
            return
        self.arc_preview.show(header, items)

    def start_arc_extract(self, scope):
        out_dir = self.arc_out.get()
        if scope != "game" and not self.entries:
            messagebox.showwarning(APP_TITLE, "Open a .arc file first.")
            return
        if not out_dir:
            messagebox.showwarning(APP_TITLE, "Choose an output folder.")
            return
        kwargs = {"resume": self.resume_var.get(), "include_audio": True,
                  "workers": int(self.workers_var.get())}
        if scope == "game":
            start = Path(self.arc_path.get()).parent if self.arc_path.get() else None
            src = filedialog.askdirectory(title="Game folder", initialdir=start)
            if not src:
                return
            if not advhd_extract.find_arcs(src, include_audio=True):
                messagebox.showwarning(APP_TITLE, "There are no .arc files in that folder.")
                return
            folder = Path(out_dir)
        else:
            src = self.arc_path.get()
            folder = Path(out_dir) / Path(src).stem
            if scope == "selected":
                names = sorted({self.entries[int(i)].name for i in self.arc_tree.selection()})
                if not names:
                    messagebox.showwarning(APP_TITLE, "No files selected.")
                    return
                kwargs["names"] = names

        def done(r):
            if scope != "game":
                self._auto(self.arc_pack_in, folder)
            text = f"{r.images} images and {r.others} other files"
            if r.skipped:
                text += f" ({r.skipped} were already done)"
            if r.warnings:
                text += f"\n\n{len(r.warnings)} warnings:\n" + "\n".join(r.warnings[:5])
            if r.errors:
                text += f"\n\n{len(r.errors)} errors:\n" + "\n".join(r.errors[:10])
            self._ask_open(f"{text}\n\nin:\n{folder}", folder)

        self.arc_preview.show()
        self.run_task(advhd_extract.extract_images, (src, out_dir), kwargs,
                      "Extracting the game" if scope == "game" else "Extracting", done)

    def browse_arc_pack_out(self):
        path = self._ask_save("Save .arc", self.arc_pack_out.get() or "new.arc", ".arc",
                              [("ARC files", "*.arc"), ("All", "*.*")])
        if path:
            self.arc_pack_out.set(path)

    def start_arc_pack(self):
        arc, folder, out = self.arc_path.get(), self.arc_pack_in.get(), self.arc_pack_out.get()
        if not folder or not Path(folder).is_dir():
            messagebox.showwarning(APP_TITLE, "Choose the folder with the files.")
            return
        extracted = False
        if self.entries:
            try:
                advhd_extract.find_images_folder(folder, arc)
                extracted = True
            except ValueError:
                pass
        if not self._check_output(out, source=arc if extracted else None):
            return
        if extracted:
            def done(r):
                if r.errors:
                    messagebox.showerror(APP_TITLE, "The file was not created. Errors:\n\n" +
                                         "\n".join(r.errors[:15]))
                    return
                changed = "\n".join(r.changed[:15]) + ("\n…" if len(r.changed) > 15 else "")
                self._ask_open(f"{len(r.changed)} of {r.entries} files replaced{':' if r.changed else '.'}\n"
                               f"{changed}\n\n{out}", Path(out).parent)

            self.run_task(advhd_extract.pack_images, (arc, folder, out),
                          {"allow_resize": self.resize_var.get()}, "Repacking", done)
        else:
            self.run_task(pack, (folder, out), {}, "Packing",
                          lambda n: self._ask_open(f"Archive created ({n} files):\n{out}", Path(out).parent))

    # ------------------------------------------------------------ Pestaña Scripts

    def _build_script_tab(self, tab):
        tab.grid_columnconfigure(1, weight=1)
        tab.grid_rowconfigure(7, weight=1)

        self.rio_path = PathRow(tab, 0, "Rio.arc:", self.browse_rio, "Open…")
        self.rio_text = PathRow(tab, 1, "Text folder:", lambda: self._browse_dir(self.rio_text, "Text folder"))
        self.rio_charmap = PathRow(tab, 2, "Character table:", self.browse_charmap)
        self._busy(self.rio_path.button, self.rio_text.button, self.rio_charmap.button)

        ext = ctk.CTkFrame(tab)
        ext.grid(row=3, column=0, columnspan=3, sticky="ew", padx=4, pady=(6, 4))
        ctk.CTkLabel(ext, text="Extract", font=ctk.CTkFont(weight="bold")).pack(side="left", padx=8, pady=6)
        self.rio_listing = tk.BooleanVar(value=False)
        self._busy(ctk.CTkCheckBox(ext, text="Also save the full listing of each script",
                                   variable=self.rio_listing)).pack(side="left", padx=8)
        self._busy(ctk.CTkButton(ext, text="Extract text", width=140, command=self.start_rio_extract)) \
            .pack(side="right", padx=8, pady=6)

        reb = ctk.CTkFrame(tab)
        reb.grid(row=4, column=0, columnspan=3, sticky="ew", padx=4, pady=(0, 4))
        reb.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(reb, text="Rebuild", font=ctk.CTkFont(weight="bold")) \
            .grid(row=0, column=0, sticky="w", padx=8, pady=(6, 0))
        self.rio_out = PathRow(reb, 1, "Output Rio.arc:", self.browse_rio_out, "Save as…")
        self._busy(self.rio_out.button)
        ctk.CTkLabel(reb, text="Mode:").grid(row=2, column=0, sticky="w", padx=(4, 8), pady=3)
        self.rio_mode = tk.StringVar(value=MODE_RELOC)
        self._busy(ctk.CTkSegmentedButton(reb, values=[MODE_RELOC, MODE_FIXED], variable=self.rio_mode)) \
            .grid(row=2, column=1, sticky="w", pady=3)
        self._busy(ctk.CTkButton(reb, text="Rebuild", width=140, command=self.start_rio_rebuild)) \
            .grid(row=2, column=2, sticky="e", padx=4, pady=(3, 8))
        muted(ctk.CTkLabel, reb, justify="left", anchor="w",
              text="Relocate: texts can be longer than the original (every jump and conditional block is moved).\n"
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

    def browse_rio(self):
        path = filedialog.askopenfilename(title="Script archive (Rio.arc)",
                                          filetypes=[("ARC files", "*.arc"), ("All", "*.*")])
        if path:
            self.rio_path.set(path)
            game = Path(path).parent
            self._auto(self.rio_text, game.parent / f"{game.name}_text")
            self._auto(self.rio_out, self._new_path(path))

    def browse_charmap(self):
        path = filedialog.askopenfilename(title="Character table (optional)",
                                          filetypes=[("Text", "*.txt"), ("All", "*.*")])
        if path:
            self.rio_charmap.set(path)

    def browse_rio_out(self):
        path = self._ask_save("Save Rio.arc", self.rio_out.get() or "Rio.arc", ".arc",
                              [("ARC files", "*.arc"), ("All", "*.*")])
        if path:
            self.rio_out.set(path)

    def _load_charmap(self):
        """Devuelve (ok, charmap)."""
        path = self.rio_charmap.get()
        if not path:
            return True, None
        try:
            return True, advhd_wsc.CharMap.load(path)
        except Exception as e:
            messagebox.showerror(APP_TITLE, f"Error in the character table:\n{e}")
            return False, None

    def start_rio_extract(self):
        rio, folder = self.rio_path.get(), self.rio_text.get()
        if not rio or not Path(rio).is_file():
            messagebox.showwarning(APP_TITLE, "Choose Rio.arc.")
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

        self.run_task(advhd_wsc.extract_strings, (rio, folder),
                      {"charmap": charmap, "with_listing": self.rio_listing.get()}, "Extracting text", done)

    def start_rio_rebuild(self):
        rio, folder, out = self.rio_path.get(), self.rio_text.get(), self.rio_out.get()
        if not rio or not Path(rio).is_file():
            messagebox.showwarning(APP_TITLE, "Choose the original Rio.arc.")
            return
        if not folder or not Path(folder).is_dir():
            messagebox.showwarning(APP_TITLE, "Choose the translated text folder.")
            return
        if not self._check_output(out, source=rio):
            return
        ok, charmap = self._load_charmap()
        if not ok:
            return
        mode = 'reloc' if self.rio_mode.get() == MODE_RELOC else 'fixed'

        def done(rep):
            self.report = rep
            text = rep.summary()
            if rep.warnings:
                text += "\n" + " · ".join(rep.warnings[:3])
            self.report_label.configure(text=text)
            self._refresh_report()
            problems = sum(1 for e in rep.entries if REPORT_FILTERS["With warnings"](e))
            msg = f"Archive created:\n{out}\n\n{rep.summary()}"
            if problems:
                msg += f"\n\n{problems} lines with warnings: check the report."
            self._ask_open(msg, Path(out).parent)

        self.run_task(advhd_wsc.rebuild, (rio, folder, out), {"charmap": charmap, "mode": mode},
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
        ready = "Ready"
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
        style.configure("AdvHD.Treeview", background=bg, fieldbackground=bg, foreground=fg,
                        rowheight=24, borderwidth=0)
        style.map("AdvHD.Treeview", background=[("selected", "#1f6aa5")], foreground=[("selected", "#ffffff")])
        style.configure("AdvHD.Treeview.Heading", background=head_bg, foreground=fg, relief="flat")
        style.map("AdvHD.Treeview.Heading", background=[("active", head_bg)])

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
        self.arc_preview.show_file(name)

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
        self.status.configure(text="Cancelling… (finishing the images in progress)")

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
