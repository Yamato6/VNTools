#!/usr/bin/env python3
"""
CatSystem2 Tools GUI - Archivos .int, imágenes HG-3 y guiones del motor CatSystem2
Probado con Grisaia no Kajitsu, Grisaia no Meikyuu y Grisaia no Rakuen (Frontwing).
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

import cs2_extract
import cs2_hg3
import cs2_int
import cs2_script
from cs2_common import Cancelled, CharMap
from cs2_crypto import find_passphrase, key_from_passphrase, native_available

APP_TITLE = "CatSystem2 Tools"
PREVIEW_SIZE = (300, 260)
PREVIEW_INTERVAL = 0.5      # segundos entre actualizaciones de la vista previa al extraer
PREVIEW_MAX_SIZE = 64 << 20
PREVIEW_TYPES = {"hg3", "png", "bmp", "jpg", "jpeg"}
MAX_REPORT_ROWS = 5000
ALL_TYPES = "All"
INT_TYPES = [("INT files", "*.int"), ("All", "*.*")]

REPORT_FILTERS = {
    "All": lambda e: True,
    "Unsupported characters": lambda e: cs2_script.ST_UNSUPPORTED in e.flags,
    "Replaced characters": lambda e: cs2_script.ST_REPLACED in e.flags,
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


def image_items(data: bytes, name: str):
    """Imágenes de una entrada: los fotogramas de un HG-3 o una imagen PNG/BMP/JPG."""
    if cs2_hg3.is_hg3(data):
        frames = cs2_hg3.decode(data)
        names = cs2_hg3.frame_names(Path(name).stem, len(frames))
        return [(img, f"{fr.width}×{fr.height}, {fr.bpp}-bit\nPosition: x={fr.x}  y={fr.y}\n"
                      f"Canvas: {fr.canvas_w}×{fr.canvas_h}\nSaved as {fname}")
                for (img, fr), fname in zip(frames, names)]
    img = Image.open(io.BytesIO(data))
    return [(img, f"{img.width}×{img.height} {img.mode}")]


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1000x740")
        self.minsize(820, 620)

        self.events = queue.Queue()
        self.cancel_event = threading.Event()
        self.worker = None
        self.busy_widgets = []
        self._autos = {}            # último valor puesto automáticamente en cada campo
        self._last_preview = 0.0
        self._auto_phrase = ''      # frase detectada (se reemplaza al cambiar de juego)
        self._phrase_origin = ''

        self.entries = []           # pestaña INT Files: lista de cs2_int.IntEntry
        self._int_arc = None        # el .int abierto (para la vista previa)
        self.report = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._build_key_bar()
        self.tabs = ctk.CTkTabview(self)
        self.tabs.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 0))
        self._build_int_tab(self.tabs.add("INT Files"))
        self._build_text_tab(self.tabs.add("Scripts"))
        self._build_status_bar()
        self._update_key_info()
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
        tree = ttk.Treeview(frame, columns=columns[1:], selectmode="extended", style="CS2.Treeview")
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
    def _new_path(int_path) -> Path:
        """<carpeta del juego>_new/<nombre>.int, junto al juego."""
        p = Path(int_path)
        return p.parent.parent / f"{p.parent.name}_new" / p.name

    # ------------------------------------------------------------ Frase del juego

    def _build_key_bar(self):
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=16, pady=(10, 0))
        bar.grid_columnconfigure(2, weight=1)
        ctk.CTkLabel(bar, text="Passphrase:").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.phrase_var = tk.StringVar()
        self.phrase_var.trace_add("write", lambda *_: self._update_key_info())
        ctk.CTkEntry(bar, textvariable=self.phrase_var, width=200,
                     placeholder_text="detected from the game folder").grid(row=0, column=1, sticky="w")
        self.key_info = muted(ctk.CTkLabel, bar, text="", anchor="w")
        self.key_info.grid(row=0, column=2, sticky="ew", padx=(10, 0))

    def _phrase(self):
        return self.phrase_var.get().strip() or None

    def _detect_phrase(self, path):
        """Busca la frase en la carpeta del juego de path, salvo que el usuario haya escrito otra."""
        current = self.phrase_var.get().strip()
        if current and current != self._auto_phrase:
            return
        try:
            phrase, origin = find_passphrase(path)
        except OSError:
            return
        if phrase:
            self._auto_phrase = phrase
            self._phrase_origin = origin
            self.phrase_var.set(phrase)

    def _update_key_info(self):
        phrase = self.phrase_var.get().strip()
        if not phrase:
            self.key_info.configure(text="Only needed for encrypted archives; detected from the game .exe")
            return
        try:
            key = key_from_passphrase(phrase)
        except UnicodeEncodeError:
            self.key_info.configure(text="Not valid: the passphrase must be Shift-JIS text")
            return
        origin = f"from {self._phrase_origin}  ·  " if phrase == self._auto_phrase else ""
        self.key_info.configure(text=f"{origin}key 0x{key:08X}")

    def _key_for(self, int_path):
        """Clave principal para un .int (None si no está cifrado). Lanza ValueError si falta."""
        if not cs2_int.is_encrypted(int_path):
            return None
        key, why = cs2_extract.resolve_key(int_path, self._phrase())
        if key is None:
            raise ValueError(f"{Path(int_path).name} is encrypted and {why}.\n"
                             "Enter the game's passphrase at the top of the window.")
        return key

    # ------------------------------------------------------------ Pestaña INT

    def _build_int_tab(self, tab):
        tab.grid_columnconfigure(1, weight=1)
        tab.grid_rowconfigure(3, weight=1)

        self.int_path = PathRow(tab, 0, ".int file:", self.browse_int, "Open…")
        self.int_path.entry.bind("<Return>", lambda _e: self.load_int(self.int_path.get()))
        self._busy(self.int_path.button)

        self.int_info = muted(ctk.CTkLabel, tab, text="No file open", anchor="w")
        self.int_info.grid(row=1, column=0, columnspan=3, sticky="ew", padx=4)

        self.int_filter = tk.StringVar()
        self.int_filter.trace_add("write", lambda *_: self._refresh_int_tree())
        ctk.CTkLabel(tab, text="Filter:").grid(row=2, column=0, sticky="w", padx=(4, 8), pady=3)
        filt = ctk.CTkFrame(tab, fg_color="transparent")
        filt.grid(row=2, column=1, columnspan=2, sticky="ew", padx=(0, 4), pady=3)
        filt.grid_columnconfigure(0, weight=1)
        ctk.CTkEntry(filt, textvariable=self.int_filter, placeholder_text="name contains…") \
            .grid(row=0, column=0, sticky="ew")
        self.int_type = tk.StringVar(value=ALL_TYPES)
        self.int_type_menu = ctk.CTkOptionMenu(filt, values=[ALL_TYPES], width=100, variable=self.int_type,
                                               command=lambda _v: self._refresh_int_tree())
        self.int_type_menu.grid(row=0, column=1, padx=(8, 0))

        body = ctk.CTkFrame(tab, fg_color="transparent")
        body.grid(row=3, column=0, columnspan=3, sticky="nsew", padx=4, pady=4)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)
        frame, self.int_tree = self._make_tree(body, ("name", "type", "size"), ("Name", "Type", "Size"),
                                               (300, 70, 100), {"size": "e"})
        frame.grid(row=0, column=0, sticky="nsew")
        self.int_tree.bind("<<TreeviewSelect>>", lambda _e: self._on_int_select())
        self.int_preview = Preview(body)
        self.int_preview.grid(row=0, column=1, sticky="ns", padx=(8, 0))
        self.int_preview.bind_keys(self.int_tree)

        self.int_out = PathRow(tab, 4, "Output:", lambda: self._browse_dir(self.int_out, "Output folder"))
        self._busy(self.int_out.button)

        actions = ctk.CTkFrame(tab, fg_color="transparent")
        actions.grid(row=5, column=0, columnspan=3, sticky="ew", padx=4, pady=(2, 0))
        self.resume_var = tk.BooleanVar(value=True)
        self._busy(ctk.CTkCheckBox(actions, text="Resume", variable=self.resume_var)).pack(side="left")
        ctk.CTkLabel(actions, text="Workers:").pack(side="left", padx=(12, 6))
        self.workers_var = tk.StringVar(value=str(cs2_extract.default_workers()))
        self._busy(ctk.CTkOptionMenu(actions, values=[str(n) for n in range(1, (os.cpu_count() or 1) + 1)],
                                     width=70, variable=self.workers_var)).pack(side="left")
        self.int_sel_label = muted(ctk.CTkLabel, actions, text="")
        self.int_sel_label.pack(side="left", padx=12)
        self._busy(ctk.CTkButton(actions, text="Extract game…", width=120,
                                 command=lambda: self.start_int_extract("game"))).pack(side="right")
        self._busy(ctk.CTkButton(actions, text="Extract all", width=110,
                                 command=lambda: self.start_int_extract("all"))).pack(side="right", padx=8)
        self._busy(ctk.CTkButton(actions, text="Extract selected", width=120,
                                 command=lambda: self.start_int_extract("selected"))).pack(side="right")
        muted(ctk.CTkLabel, tab, anchor="w",
              text="HG-3 images are saved as PNG (NAME.png, or NAME_00.png… with frames) and the rest "
                   "decrypted, in a folder per .int.") \
            .grid(row=6, column=0, columnspan=3, sticky="ew", padx=4, pady=(2, 0))

        pack = self._section(tab, 7, "Pack")
        self.int_pack_in = PathRow(pack, 1, "Folder:", lambda: self._browse_dir(self.int_pack_in,
                                                                                 "Folder with the files"))
        self.int_pack_out = PathRow(pack, 2, ".int file:", self.browse_int_pack_out, "Save as…")
        self._busy(self.int_pack_in.button, self.int_pack_out.button)
        self.add_var = tk.BooleanVar(value=False)
        self._busy(ctk.CTkCheckBox(pack, text="Also add new files from the folder", variable=self.add_var)) \
            .grid(row=3, column=1, sticky="w")
        self._busy(ctk.CTkButton(pack, text="Pack", width=140, command=self.start_int_pack)) \
            .grid(row=3, column=2, sticky="e", padx=4, pady=(2, 8))
        muted(ctk.CTkLabel, pack, anchor="w", justify="left",
              text="Creates a complete .int, encrypted like the open one: an extracted folder only re-encodes "
                   "the edited files\n(images must keep their size); any other folder is packed as it is.") \
            .grid(row=4, column=0, columnspan=3, sticky="ew", padx=8, pady=(0, 6))

    def browse_int(self):
        path = filedialog.askopenfilename(title="Open .int file", filetypes=INT_TYPES)
        if path:
            self.load_int(path)

    def load_int(self, path):
        path = path.strip().strip('"')
        if not path:
            return
        self._detect_phrase(path)
        try:
            arc = cs2_int.IntArchive(path, self._key_for(path))
        except Exception as e:
            messagebox.showerror(APP_TITLE, f"Could not read the file:\n{e}")
            return
        if self._int_arc is not None:
            self._int_arc.close()
        self._int_arc = arc
        self.entries = arc.entries
        self.int_path.set(path)
        p = Path(path)
        self._auto(self.int_out, p.parent.parent / f"{p.parent.name}_extracted")
        self._auto(self.int_pack_in, Path(self.int_out.get()) / p.stem)
        self._auto(self.int_pack_out, self._new_path(path))

        types = Counter(e.ext or '(none)' for e in self.entries)
        total = sum(e.size for e in self.entries)
        self.int_info.configure(text=f"{len(self.entries)} files  ·  {format_size(total)}  ·  "
                                     f"{'encrypted' if arc.encrypted else 'not encrypted'}  ·  "
                                     + ", ".join(f"{n} {ext}" for ext, n in types.most_common()))
        self.int_type_menu.configure(values=[ALL_TYPES] + sorted(types))
        self.int_type.set(ALL_TYPES)
        self._refresh_int_tree()
        self.int_preview.show()

    def _refresh_int_tree(self):
        self.int_tree.delete(*self.int_tree.get_children())
        text = self.int_filter.get().lower()
        ext = self.int_type.get()
        for i, e in enumerate(self.entries):
            if text in e.name.lower() and ext in (ALL_TYPES, e.ext or '(none)'):
                self.int_tree.insert("", "end", iid=str(i), text=e.name, values=(e.ext, format_size(e.size)))
        self._update_int_sel()

    def _update_int_sel(self):
        if not self.entries:
            self.int_sel_label.configure(text="")
            return
        self.int_sel_label.configure(
            text=f"{len(self.int_tree.get_children())} shown · {len(self.int_tree.selection())} selected")

    def _on_int_select(self):
        self._update_int_sel()
        sel = self.int_tree.selection()
        if len(sel) != 1:
            return
        e = self.entries[int(sel[0])]
        header = f"{e.name}\n{e.ext.upper() or 'File'} · {format_size(e.size)}"
        if e.ext not in PREVIEW_TYPES or e.size > PREVIEW_MAX_SIZE:
            self.int_preview.show(header)
            return
        try:
            items = image_items(self._int_arc.read(e), e.name)
        except Exception as ex:
            self.int_preview.show(f"{header}\n\nCould not show it:\n{ex}")
            return
        self.int_preview.show(header, items)

    def start_int_extract(self, scope):
        out_dir = self.int_out.get()
        if scope != "game" and not self.entries:
            messagebox.showwarning(APP_TITLE, "Open a .int file first.")
            return
        if not out_dir:
            messagebox.showwarning(APP_TITLE, "Choose an output folder.")
            return
        kwargs = {"resume": self.resume_var.get(), "phrase": self._phrase(),
                  "workers": int(self.workers_var.get())}
        if scope == "game":
            start = Path(self.int_path.get()).parent if self.int_path.get() else None
            src = filedialog.askdirectory(title="Game folder", initialdir=start)
            if not src:
                return
            self._detect_phrase(src)
            kwargs["phrase"] = self._phrase()
            if not cs2_extract.find_ints(src):
                messagebox.showwarning(APP_TITLE, "There are no .int files in that folder.")
                return
            folder = Path(out_dir)
        else:
            src = self.int_path.get()
            folder = Path(out_dir) / Path(src).stem
            if scope == "selected":
                names = [self.entries[int(i)].name for i in self.int_tree.selection()]
                if not names:
                    messagebox.showwarning(APP_TITLE, "No files selected.")
                    return
                kwargs["names"] = names

        def done(r):
            if scope != "game":
                self._auto(self.int_pack_in, folder)
            text = f"{r.files} files ({r.converted} images converted to PNG)"
            if r.skipped:
                text += f" ({r.skipped} were already done)"
            if r.warnings:
                text += f"\n\n{len(r.warnings)} warnings:\n" + "\n".join(r.warnings[:5])
            if r.errors:
                text += f"\n\n{len(r.errors)} errors:\n" + "\n".join(r.errors[:10])
            self._ask_open(f"{text}\n\nin:\n{folder}", folder)

        self.int_preview.show()
        self.run_task(cs2_extract.extract_all, (src, out_dir), kwargs,
                      "Extracting the game" if scope == "game" else "Extracting", done)

    def browse_int_pack_out(self):
        path = self._ask_save("Save .int", self.int_pack_out.get() or "new.int", ".int", INT_TYPES)
        if path:
            self.int_pack_out.set(path)

    def start_int_pack(self):
        int_path, folder, out = self.int_path.get(), self.int_pack_in.get(), self.int_pack_out.get()
        if not self.entries:
            messagebox.showwarning(APP_TITLE, "Open the original .int first.")
            return
        if not folder or not Path(folder).is_dir():
            messagebox.showwarning(APP_TITLE, "Choose the folder with the files.")
            return
        if not self._check_output(out, source=int_path):
            return
        try:
            cs2_extract.find_files_folder(folder, int_path)
        except ValueError:
            # Carpeta sin extraer con esta herramienta: se empaqueta tal cual, como el abierto
            try:
                key = self._key_for(int_path)
            except Exception as e:
                messagebox.showerror(APP_TITLE, str(e))
                return
            self.run_task(cs2_int.pack, (folder, out), {"like": int_path, "key": key}, "Packing",
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

        self.run_task(cs2_extract.repack, (int_path, folder, out),
                      {"add_new": self.add_var.get(), "phrase": self._phrase()}, "Repacking", done)

    # ------------------------------------------------------------ Pestaña Scripts

    def _build_text_tab(self, tab):
        tab.grid_columnconfigure(1, weight=1)
        tab.grid_rowconfigure(7, weight=1)

        self.tx_int = PathRow(tab, 0, "scene.int:", self.browse_tx_int, "Open…")
        self.tx_text = PathRow(tab, 1, "Text folder:", lambda: self._browse_dir(self.tx_text, "Text folder"))
        self.tx_charmap = PathRow(tab, 2, "Character table:", self.browse_charmap)
        self._busy(self.tx_int.button, self.tx_text.button, self.tx_charmap.button)

        ext = ctk.CTkFrame(tab)
        ext.grid(row=3, column=0, columnspan=3, sticky="ew", padx=4, pady=(6, 4))
        ctk.CTkLabel(ext, text="Extract", font=ctk.CTkFont(weight="bold")).pack(side="left", padx=8, pady=6)
        self.tx_json = tk.BooleanVar(value=False)
        self._busy(ctk.CTkCheckBox(ext, text="JSON (with the voice of each line)", variable=self.tx_json)) \
            .pack(side="left", padx=8)
        self._busy(ctk.CTkButton(ext, text="Extract text", width=140, command=self.start_tx_extract)) \
            .pack(side="right", padx=8, pady=6)

        reb = ctk.CTkFrame(tab)
        reb.grid(row=4, column=0, columnspan=3, sticky="ew", padx=4, pady=(0, 4))
        reb.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(reb, text="Rebuild", font=ctk.CTkFont(weight="bold")) \
            .grid(row=0, column=0, sticky="w", padx=8, pady=(6, 0))
        self.tx_out = PathRow(reb, 1, "Output .int:", self.browse_tx_out, "Save as…")
        self._busy(self.tx_out.button)
        muted(ctk.CTkLabel, reb, anchor="w",
              text="Lines can be any length. Scripts without changes are copied unchanged.") \
            .grid(row=2, column=1, sticky="w")
        self._busy(ctk.CTkButton(reb, text="Rebuild", width=140, command=self.start_tx_rebuild)) \
            .grid(row=2, column=2, sticky="e", padx=4, pady=(3, 8))

        bar = ctk.CTkFrame(tab, fg_color="transparent")
        bar.grid(row=5, column=0, columnspan=3, sticky="ew", padx=4)
        self.report_label = muted(ctk.CTkLabel, bar, text="The report will appear here after rebuilding.",
                                  anchor="w", justify="left", wraplength=640)
        self.report_label.pack(side="left", fill="x", expand=True)
        self.report_filter = ctk.CTkOptionMenu(bar, values=list(REPORT_FILTERS), width=200,
                                               command=lambda _v: self._refresh_report())
        self.report_filter.pack(side="right")

        frame, self.report_tree = self._make_tree(
            tab, ("entry", "key", "state", "detail"), ("Script", "Line", "Status", "Detail"), (160, 90, 200, 0))
        frame.grid(row=7, column=0, columnspan=3, sticky="nsew", padx=4, pady=4)

    def browse_tx_int(self):
        path = filedialog.askopenfilename(title="scene.int", filetypes=INT_TYPES)
        if path:
            self.tx_int.set(path)
            self._detect_phrase(path)
            game = Path(path).parent
            self._auto(self.tx_text, game.parent / f"{game.name}_text")
            self._auto(self.tx_out, self._new_path(path))

    def browse_charmap(self):
        path = filedialog.askopenfilename(title="Character table (optional)",
                                          filetypes=[("Text", "*.txt"), ("All", "*.*")])
        if path:
            self.tx_charmap.set(path)

    def browse_tx_out(self):
        path = self._ask_save("Save .int", self.tx_out.get() or "scene.int", ".int", INT_TYPES)
        if path:
            self.tx_out.set(path)

    def _prepare(self, int_path):
        """Devuelve (ok, charmap, clave)."""
        try:
            path = self.tx_charmap.get()
            charmap = CharMap.load(path) if path else None
            return True, charmap, self._key_for(int_path)
        except Exception as e:
            messagebox.showerror(APP_TITLE, str(e))
            return False, None, None

    def start_tx_extract(self):
        int_path, folder = self.tx_int.get(), self.tx_text.get()
        if not int_path or not Path(int_path).is_file():
            messagebox.showwarning(APP_TITLE, "Choose scene.int.")
            return
        if not folder:
            messagebox.showwarning(APP_TITLE, "Choose the text folder.")
            return
        ok, charmap, key = self._prepare(int_path)
        if not ok:
            return

        def done(r):
            text = (f"{r.lines} lines from {r.files} scripts. One file per script: LINE|TEXT; translate only "
                    "the text after '|' and keep the codes (\\n, \\@, [ ]).")
            if r.warnings:
                text += "\n" + " · ".join(r.warnings[:3])
            self.report_label.configure(text=text)
            self._ask_open(f"Extracted {r.lines} lines from {r.files} scripts to:\n{folder}", folder)

        self.run_task(cs2_script.extract_text, (int_path, folder),
                      {"key": key, "charmap": charmap, "with_json": self.tx_json.get()}, "Extracting text", done)

    def start_tx_rebuild(self):
        int_path, folder, out = self.tx_int.get(), self.tx_text.get(), self.tx_out.get()
        if not int_path or not Path(int_path).is_file():
            messagebox.showwarning(APP_TITLE, "Choose scene.int.")
            return
        if not folder or not Path(folder).is_dir():
            messagebox.showwarning(APP_TITLE, "Choose the translated text folder.")
            return
        if not self._check_output(out, source=int_path):
            return
        ok, charmap, key = self._prepare(int_path)
        if not ok:
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
                msg = f"Archive created ({rep.output_entries} files):\n{out}\n\n{msg}"
            if rep.entries:
                msg += f"\n\n{len(rep.entries)} lines with notes: check the report."
            self._ask_open(msg, Path(out).parent)

        self.run_task(cs2_script.rebuild, (int_path, folder, out), {"key": key, "charmap": charmap},
                      "Rebuilding", done)

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
        bar.grid(row=2, column=0, sticky="ew", padx=12, pady=12)
        bar.grid_columnconfigure(1, weight=1)

        self.progress = ctk.CTkProgressBar(bar)
        self.progress.set(0)
        self.progress.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(10, 4))
        ready = "Ready" if native_available() else "Ready (_cs2.dll not found: using the slower Python version)"
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
        style.configure("CS2.Treeview", background=bg, fieldbackground=bg, foreground=fg,
                        rowheight=24, borderwidth=0)
        style.map("CS2.Treeview", background=[("selected", "#1f6aa5")], foreground=[("selected", "#ffffff")])
        style.configure("CS2.Treeview.Heading", background=head_bg, foreground=fg, relief="flat")
        style.map("CS2.Treeview.Heading", background=[("active", head_bg)])

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
        self.int_preview.show_file(name)

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
    multiprocessing.freeze_support()
    main()
