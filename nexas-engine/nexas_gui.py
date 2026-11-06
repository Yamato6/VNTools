#!/usr/bin/env python3
"""
NeXAS Tools GUI - Archivos .pac, imágenes y textos del motor NeXAS (Giga)
Juego: Maji de Watashi ni Koi Shinasai! S (Minato Soft, 2012)
"""

import io
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

import nexas_extract
import nexas_pac
import nexas_script
from nexas_common import Cancelled
from nexas_formats import compose_spm, fil_to_image, nmv_frame_image, read_nmv, read_spm

APP_TITLE = "NeXAS Tools"
PREVIEW_SIZE = (300, 260)
PREVIEW_INTERVAL = 0.5      # segundos entre actualizaciones de la vista previa al extraer
PREVIEW_MAX_SIZE = 64 << 20
MAX_REPORT_ROWS = 5000
ALL_TYPES = "All"
PAC_TYPES = [("PAC files", "*.pac"), ("All", "*.*")]
IMAGE_EXTS = {"png", "bmp", "jpg", "jpeg"}

REPORT_FILTERS = {
    "All": lambda e: True,
    "Unsupported characters": lambda e: nexas_script.ST_UNSUPPORTED in e.flags,
    "Replaced characters": lambda e: nexas_script.ST_REPLACED in e.flags,
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


def image_items(pac: nexas_pac.Pac, e: nexas_pac.PacEntry):
    """Imágenes de una entrada: PNG/BMP/JPG, FIL, fotogramas de NMV o patrones de SPM."""
    data = pac.read(e)
    if e.ext in IMAGE_EXTS:
        img = Image.open(io.BytesIO(data))
        return [(img, f"{img.width}×{img.height} {img.mode}")]
    if e.ext == 'fil':
        img = fil_to_image(data)
        return [(img, f"{img.width}×{img.height} mask\nSaved as {e.name.rsplit('.', 1)[0]}.fil.png")]
    if e.ext == 'nmv':
        nmv = read_nmv(data)
        if nmv.version != 4:
            return []
        return [(lambda fr=fr: nmv_frame_image(nmv, fr), f"{nmv.width}×{nmv.height}, frame at {fr.time} ms")
                for fr in nmv.frames]
    if e.ext == 'spm':
        spm = read_spm(data)
        by_name = {x.name.lower(): x for x in pac.entries}
        cache = {}

        def load(name):
            key = name.lower()
            if key not in cache:
                entry = by_name.get(key)
                cache[key] = Image.open(io.BytesIO(pac.read(entry))) if entry else None
            return cache[key]

        return [(lambda k=k: compose_spm(spm, k, load), f"Pattern {k}: {pat.size[0]}×{pat.size[1]}")
                for k, pat in enumerate(spm.patterns) if pat.size[0] > 0 and pat.size[1] > 0 and pat.parts]
    return []


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

        self.entries = []           # pestaña PAC Files: lista de nexas_pac.PacEntry
        self._pac = None            # el .pac abierto (para la vista previa)
        self.report = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.tabs = ctk.CTkTabview(self)
        self.tabs.grid(row=0, column=0, sticky="nsew", padx=12, pady=(6, 0))
        self._build_pac_tab(self.tabs.add("PAC Files"))
        self._build_text_tab(self.tabs.add("Text"))
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
        tree = ttk.Treeview(frame, columns=columns[1:], selectmode="extended", style="NeXAS.Treeview")
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
    def _free_update_name(game: Path) -> str:
        """El primer UpdateN.pac (4..8) que no existe en la carpeta del juego."""
        for n in range(4, 9):
            if not (game / f"Update{n}.pac").exists():
                return f"Update{n}.pac"
        return "Update8.pac"

    @staticmethod
    def _new_dir(pac_path) -> Path:
        """<carpeta del juego>_new, junto al juego."""
        game = Path(pac_path).parent
        return game.parent / f"{game.name}_new"

    # ------------------------------------------------------------ Pestaña PAC

    def _build_pac_tab(self, tab):
        tab.grid_columnconfigure(1, weight=1)
        tab.grid_rowconfigure(3, weight=1)

        self.pac_path = PathRow(tab, 0, ".pac file:", self.browse_pac, "Open…")
        self.pac_path.entry.bind("<Return>", lambda _e: self.load_pac(self.pac_path.get()))
        self._busy(self.pac_path.button)

        self.pac_info = muted(ctk.CTkLabel, tab, text="No file open", anchor="w")
        self.pac_info.grid(row=1, column=0, columnspan=3, sticky="ew", padx=4)

        self.pac_filter = tk.StringVar()
        self.pac_filter.trace_add("write", lambda *_: self._refresh_pac_tree())
        ctk.CTkLabel(tab, text="Filter:").grid(row=2, column=0, sticky="w", padx=(4, 8), pady=3)
        filt = ctk.CTkFrame(tab, fg_color="transparent")
        filt.grid(row=2, column=1, columnspan=2, sticky="ew", padx=(0, 4), pady=3)
        filt.grid_columnconfigure(0, weight=1)
        ctk.CTkEntry(filt, textvariable=self.pac_filter, placeholder_text="name contains…") \
            .grid(row=0, column=0, sticky="ew")
        self.pac_type = tk.StringVar(value=ALL_TYPES)
        self.pac_type_menu = ctk.CTkOptionMenu(filt, values=[ALL_TYPES], width=100, variable=self.pac_type,
                                               command=lambda _v: self._refresh_pac_tree())
        self.pac_type_menu.grid(row=0, column=1, padx=(8, 0))

        body = ctk.CTkFrame(tab, fg_color="transparent")
        body.grid(row=3, column=0, columnspan=3, sticky="nsew", padx=4, pady=4)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)
        frame, self.pac_tree = self._make_tree(body, ("name", "type", "size"), ("Name", "Type", "Size"),
                                               (300, 70, 100), {"size": "e"})
        frame.grid(row=0, column=0, sticky="nsew")
        self.pac_tree.bind("<<TreeviewSelect>>", lambda _e: self._on_pac_select())
        self.pac_preview = Preview(body)
        self.pac_preview.grid(row=0, column=1, sticky="ns", padx=(8, 0))
        self.pac_preview.bind_keys(self.pac_tree)

        self.pac_out = PathRow(tab, 4, "Output:", lambda: self._browse_dir(self.pac_out, "Output folder"))
        self._busy(self.pac_out.button)

        actions = ctk.CTkFrame(tab, fg_color="transparent")
        actions.grid(row=5, column=0, columnspan=3, sticky="ew", padx=4, pady=(2, 0))
        self.resume_var = tk.BooleanVar(value=True)
        self.extras_var = tk.BooleanVar(value=False)
        self._busy(ctk.CTkCheckBox(actions, text="Resume", variable=self.resume_var)).pack(side="left")
        self._busy(ctk.CTkCheckBox(actions, text="Viewing extras", variable=self.extras_var)) \
            .pack(side="left", padx=(12, 0))
        self.pac_sel_label = muted(ctk.CTkLabel, actions, text="")
        self.pac_sel_label.pack(side="left", padx=12)
        self._busy(ctk.CTkButton(actions, text="Extract game…", width=120,
                                 command=lambda: self.start_pac_extract("game"))).pack(side="right")
        self._busy(ctk.CTkButton(actions, text="Extract all", width=110,
                                 command=lambda: self.start_pac_extract("all"))).pack(side="right", padx=8)
        self._busy(ctk.CTkButton(actions, text="Extract selected", width=120,
                                 command=lambda: self.start_pac_extract("selected"))).pack(side="right")
        muted(ctk.CTkLabel, tab, anchor="w",
              text="FIL masks are saved as PNG and the rest as is, in a folder per .pac. Viewing extras "
                   "(NMV frames, sprites, CSV) are not repacked.") \
            .grid(row=6, column=0, columnspan=3, sticky="ew", padx=4, pady=(2, 0))

        pack = self._section(tab, 7, "Pack")
        self.pac_pack_in = PathRow(pack, 1, "Folder:", lambda: self._browse_dir(self.pac_pack_in,
                                                                                 "Folder with the files"))
        self.pac_pack_out = PathRow(pack, 2, ".pac file:", self.browse_pac_pack_out, "Save as…")
        self._busy(self.pac_pack_in.button, self.pac_pack_out.button)
        opts = ctk.CTkFrame(pack, fg_color="transparent")
        opts.grid(row=3, column=1, sticky="w")
        self.patch_var = tk.BooleanVar(value=True)
        self.add_var = tk.BooleanVar(value=False)
        self.resize_var = tk.BooleanVar(value=False)
        self._busy(ctk.CTkCheckBox(opts, text="Only the changed files (patch)", variable=self.patch_var,
                                   command=self._pack_defaults)).pack(side="left")
        self._busy(ctk.CTkCheckBox(opts, text="Add new files", variable=self.add_var)).pack(side="left", padx=12)
        self._busy(ctk.CTkCheckBox(opts, text="Allow a different image size", variable=self.resize_var)) \
            .pack(side="left")
        self._busy(ctk.CTkButton(pack, text="Pack", width=140, command=self.start_pac_pack)) \
            .grid(row=3, column=2, sticky="e", padx=4, pady=(2, 8))
        muted(ctk.CTkLabel, pack, anchor="w", justify="left",
              text="An extracted folder is repacked over the open .pac; a patch (Update4.pac … Update8.pac) "
                   "overrides only\nthe files it contains. Any other folder becomes a new .pac with the "
                   "order and compression of the open one.") \
            .grid(row=4, column=0, columnspan=3, sticky="ew", padx=8, pady=(0, 6))

    def browse_pac(self):
        path = filedialog.askopenfilename(title="Open .pac file", filetypes=PAC_TYPES)
        if path:
            self.load_pac(path)

    def load_pac(self, path):
        path = path.strip().strip('"')
        if not path:
            return
        try:
            pac = nexas_pac.Pac(path)
        except Exception as e:
            messagebox.showerror(APP_TITLE, f"Could not read the file:\n{e}")
            return
        if self._pac is not None:
            self._pac.close()
        self._pac = pac
        self.entries = pac.entries
        self.pac_path.set(path)
        p = Path(path)
        self._auto(self.pac_out, p.parent.parent / f"{p.parent.name}_extracted")
        self._auto(self.pac_pack_in, Path(self.pac_out.get()) / p.stem)
        self._pack_defaults()

        types = Counter(e.ext or '(none)' for e in self.entries)
        total = sum(e.size for e in self.entries)
        self.pac_info.configure(text=f"{len(self.entries)} files  ·  {format_size(total)}  ·  "
                                     f"compression: {nexas_pac.PACK_NAMES[pac.pack]}  ·  "
                                     + ", ".join(f"{n} {ext}" for ext, n in types.most_common()))
        self.pac_type_menu.configure(values=[ALL_TYPES] + sorted(types))
        self.pac_type.set(ALL_TYPES)
        self._refresh_pac_tree()
        self.pac_preview.show()

    def _pack_defaults(self):
        pac = self.pac_path.get()
        if pac:
            p = Path(pac)
            name = self._free_update_name(p.parent) if self.patch_var.get() else p.name
            self._auto(self.pac_pack_out, self._new_dir(pac) / name)

    def _refresh_pac_tree(self):
        self.pac_tree.delete(*self.pac_tree.get_children())
        text = self.pac_filter.get().lower()
        ext = self.pac_type.get()
        for i, e in enumerate(self.entries):
            if text in e.name.lower() and ext in (ALL_TYPES, e.ext or '(none)'):
                self.pac_tree.insert("", "end", iid=str(i), text=e.name, values=(e.ext, format_size(e.size)))
        self._update_pac_sel()

    def _update_pac_sel(self):
        if not self.entries:
            self.pac_sel_label.configure(text="")
            return
        self.pac_sel_label.configure(
            text=f"{len(self.pac_tree.get_children())} shown · {len(self.pac_tree.selection())} selected")

    def _on_pac_select(self):
        self._update_pac_sel()
        sel = self.pac_tree.selection()
        if len(sel) != 1:
            return
        e = self.entries[int(sel[0])]
        header = f"{e.name}\n{e.ext.upper() or 'File'} · {format_size(e.size)}"
        if e.size > PREVIEW_MAX_SIZE:
            self.pac_preview.show(header)
            return
        try:
            items = image_items(self._pac, e)
        except Exception as ex:
            self.pac_preview.show(f"{header}\n\nCould not show it:\n{ex}")
            return
        self.pac_preview.show(header, items)

    def start_pac_extract(self, scope):
        out_dir = self.pac_out.get()
        if scope != "game" and not self.entries:
            messagebox.showwarning(APP_TITLE, "Open a .pac file first.")
            return
        if not out_dir:
            messagebox.showwarning(APP_TITLE, "Choose an output folder.")
            return
        extras = self.extras_var.get()
        kwargs = {"resume": self.resume_var.get(), "extras": extras, "sprites": extras}
        if scope == "game":
            start = Path(self.pac_path.get()).parent if self.pac_path.get() else None
            src = filedialog.askdirectory(title="Game folder", initialdir=start)
            if not src:
                return
            if not nexas_extract.find_pacs(src):
                messagebox.showwarning(APP_TITLE, "There are no .pac files in that folder.")
                return
            folder = Path(out_dir)
        else:
            src = self.pac_path.get()
            folder = Path(out_dir) / Path(src).stem
            if scope == "selected":
                names = [self.entries[int(i)].name for i in self.pac_tree.selection()]
                if not names:
                    messagebox.showwarning(APP_TITLE, "No files selected.")
                    return
                kwargs["names"] = names

        def done(r):
            if scope != "game":
                self._auto(self.pac_pack_in, folder)
            text = f"{r.files} files ({r.converted} converted)"
            if r.extras:
                text += f" and {r.extras} extra files"
            if r.skipped:
                text += f" ({r.skipped} were already done)"
            if r.warnings:
                text += f"\n\n{len(r.warnings)} warnings:\n" + "\n".join(r.warnings[:5])
            if r.errors:
                text += f"\n\n{len(r.errors)} errors:\n" + "\n".join(r.errors[:10])
            self._ask_open(f"{text}\n\nin:\n{folder}", folder)

        self.pac_preview.show()
        self.run_task(nexas_extract.extract_all, (src, out_dir), kwargs,
                      "Extracting the game" if scope == "game" else "Extracting", done)

    def browse_pac_pack_out(self):
        path = self._ask_save("Save .pac", self.pac_pack_out.get() or "Update4.pac", ".pac", PAC_TYPES)
        if path:
            self.pac_pack_out.set(path)

    def start_pac_pack(self):
        pac, folder, out = self.pac_path.get(), self.pac_pack_in.get(), self.pac_pack_out.get()
        if not self.entries:
            messagebox.showwarning(APP_TITLE, "Open the original .pac first.")
            return
        if not folder or not Path(folder).is_dir():
            messagebox.showwarning(APP_TITLE, "Choose the folder with the files.")
            return
        if not self._check_output(out, source=pac):
            return
        try:
            nexas_extract.find_files_folder(folder, pac)
        except ValueError:
            # Carpeta sin extraer con esta herramienta: un .pac nuevo como el abierto
            self.run_task(nexas_pac.pack, (folder, out), {"order_from": pac}, "Packing",
                          lambda n: self._ask_open(f"Archive created ({n} files):\n{out}", Path(out).parent))
            return

        def done(r):
            if r.errors:
                messagebox.showerror(APP_TITLE, "The file was not created. Errors:\n\n" + "\n".join(r.errors[:15]))
                return
            if not r.entries:
                messagebox.showinfo(APP_TITLE, "No file was edited since the extraction: no file was written.")
                return
            changed = r.changed + [f"{n} (new)" for n in r.added]
            listing = "\n".join(changed[:15]) + ("\n…" if len(changed) > 15 else "")
            self._ask_open(f"{len(r.changed)} files replaced, {len(r.added)} added:\n{listing}\n\n"
                           f"{out} ({r.entries} files)", Path(out).parent)

        self.run_task(nexas_extract.repack, (pac, folder, out),
                      {"patch_only": self.patch_var.get(), "add_new": self.add_var.get(),
                       "allow_resize": self.resize_var.get()}, "Repacking", done)

    # ------------------------------------------------------------ Pestaña Text

    def _build_text_tab(self, tab):
        tab.grid_columnconfigure(1, weight=1)
        tab.grid_rowconfigure(8, weight=1)

        self.tx_pac = PathRow(tab, 0, "Script .pac:", self.browse_tx_pac, "Open…")
        self.tx_ref = PathRow(tab, 1, "Reference .pac:", self.browse_tx_ref)
        self.tx_text = PathRow(tab, 2, "Text folder:", lambda: self._browse_dir(self.tx_text, "Text folder"))
        self.tx_charmap = PathRow(tab, 3, "Character table:", self.browse_charmap)
        self._busy(self.tx_pac.button, self.tx_ref.button, self.tx_text.button, self.tx_charmap.button)

        ext = ctk.CTkFrame(tab)
        ext.grid(row=4, column=0, columnspan=3, sticky="ew", padx=4, pady=(6, 4))
        ctk.CTkLabel(ext, text="Extract", font=ctk.CTkFont(weight="bold")).pack(side="left", padx=8, pady=6)
        self.tx_listing = tk.BooleanVar(value=False)
        self.tx_json = tk.BooleanVar(value=False)
        self._busy(ctk.CTkCheckBox(ext, text="Script listing", variable=self.tx_listing)).pack(side="left", padx=8)
        self._busy(ctk.CTkCheckBox(ext, text="JSON", variable=self.tx_json)).pack(side="left", padx=8)
        self._busy(ctk.CTkButton(ext, text="Extract text", width=140, command=self.start_tx_extract)) \
            .pack(side="right", padx=8, pady=6)

        reb = ctk.CTkFrame(tab)
        reb.grid(row=5, column=0, columnspan=3, sticky="ew", padx=4, pady=(0, 4))
        reb.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(reb, text="Rebuild", font=ctk.CTkFont(weight="bold")) \
            .grid(row=0, column=0, sticky="w", padx=8, pady=(6, 0))
        self.tx_out = PathRow(reb, 1, "Output .pac:", self.browse_tx_out, "Save as…")
        self._busy(self.tx_out.button)
        self.tx_patch = tk.BooleanVar(value=True)
        self._busy(ctk.CTkCheckBox(reb, text="Only the changed files (patch)", variable=self.tx_patch)) \
            .grid(row=2, column=1, sticky="w", pady=3)
        self._busy(ctk.CTkButton(reb, text="Rebuild", width=140, command=self.start_tx_rebuild)) \
            .grid(row=2, column=2, sticky="e", padx=4, pady=(3, 8))
        muted(ctk.CTkLabel, reb, justify="left", anchor="w",
              text="Lines can be any length. The reference .pac (optional, e.g. Update3.pac in English) adds "
                   "its text to each line\nas a '#>' comment.") \
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
            tab, ("entry", "key", "state", "detail"), ("File", "Line", "Status", "Detail"), (160, 90, 200, 0))
        frame.grid(row=8, column=0, columnspan=3, sticky="nsew", padx=4, pady=4)

    def browse_tx_pac(self):
        path = filedialog.askopenfilename(title="Script.pac, Update3.pac or Config.pac", filetypes=PAC_TYPES)
        if path:
            self.tx_pac.set(path)
            p = Path(path)
            game = p.parent
            self._auto(self.tx_text, game.parent / f"{game.name}_text")
            if p.stem.lower() == 'script' and (game / "Update3.pac").exists():
                self._auto(self.tx_ref, game / "Update3.pac")
            self._auto(self.tx_out, self._new_dir(path) / self._free_update_name(game))

    def browse_tx_ref(self):
        path = filedialog.askopenfilename(title="Reference .pac (optional)", filetypes=PAC_TYPES)
        if path:
            self.tx_ref.set(path)

    def browse_charmap(self):
        path = filedialog.askopenfilename(title="Character table (optional)",
                                          filetypes=[("Text", "*.txt"), ("All", "*.*")])
        if path:
            self.tx_charmap.set(path)

    def browse_tx_out(self):
        path = self._ask_save("Save .pac", self.tx_out.get() or "Update4.pac", ".pac", PAC_TYPES)
        if path:
            self.tx_out.set(path)

    def _load_charmap(self):
        """Devuelve (ok, charmap)."""
        path = self.tx_charmap.get()
        if not path:
            return True, None
        try:
            return True, nexas_script.CharMap.load(path)
        except Exception as e:
            messagebox.showerror(APP_TITLE, f"Error in the character table:\n{e}")
            return False, None

    def start_tx_extract(self):
        pac, ref, folder = self.tx_pac.get(), self.tx_ref.get(), self.tx_text.get()
        if not pac or not Path(pac).is_file():
            messagebox.showwarning(APP_TITLE, "Choose the script .pac.")
            return
        if ref and not Path(ref).is_file():
            messagebox.showwarning(APP_TITLE, "The reference .pac does not exist.")
            return
        if not folder:
            messagebox.showwarning(APP_TITLE, "Choose the text folder.")
            return
        ok, charmap = self._load_charmap()
        if not ok:
            return

        def done(r):
            text = (f"{r.lines} lines from {r.files} files. One file per script: INDEX|TEXT "
                    "(tables: ROW:COLUMN|TEXT); translate only the text.")
            if r.warnings:
                text += "\n" + " · ".join(r.warnings[:3])
            self.report_label.configure(text=text)
            self._ask_open(f"Extracted {r.lines} lines from {r.files} files to:\n{folder}", folder)

        self.run_task(nexas_script.extract_text, (pac, folder),
                      {"reference": ref or None, "charmap": charmap, "with_listing": self.tx_listing.get(),
                       "with_json": self.tx_json.get()}, "Extracting text", done)

    def start_tx_rebuild(self):
        pac, folder, out = self.tx_pac.get(), self.tx_text.get(), self.tx_out.get()
        if not pac or not Path(pac).is_file():
            messagebox.showwarning(APP_TITLE, "Choose the script .pac.")
            return
        if not folder or not Path(folder).is_dir():
            messagebox.showwarning(APP_TITLE, "Choose the translated text folder.")
            return
        if not self._check_output(out, source=pac):
            return
        ok, charmap = self._load_charmap()
        if not ok:
            return

        def done(rep):
            self.report = rep
            text = rep.summary()
            if rep.warnings:
                text += "\n" + " · ".join(rep.warnings[:3])
            self.report_label.configure(text=text)
            self._refresh_report()
            msg = f"{rep.summary()}"
            if rep.output_entries:
                msg = f"Archive created ({rep.output_entries} files):\n{out}\n\n{msg}"
            if rep.entries:
                msg += f"\n\n{len(rep.entries)} lines with notes: check the report."
            self._ask_open(msg, Path(out).parent)

        self.run_task(nexas_script.rebuild, (pac, folder, out),
                      {"charmap": charmap, "patch_only": self.tx_patch.get()}, "Rebuilding", done)

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
        style.configure("NeXAS.Treeview", background=bg, fieldbackground=bg, foreground=fg,
                        rowheight=24, borderwidth=0)
        style.map("NeXAS.Treeview", background=[("selected", "#1f6aa5")], foreground=[("selected", "#ffffff")])
        style.configure("NeXAS.Treeview.Heading", background=head_bg, foreground=fg, relief="flat")
        style.map("NeXAS.Treeview.Heading", background=[("active", head_bg)])

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
        self.pac_preview.show_file(name)

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
        self.status.configure(text="Cancelling…")

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
    main()
