#!/usr/bin/env python3
"""
CatSystem2 Tools GUI - Interfaz gráfica para los archivos .int del motor CatSystem2
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
PREVIEW_SIZE = (360, 270)
PANEL_SIZE = (300, 260)     # vista previa de la pestaña INT Files
PREVIEW_TYPES = {"hg3", "png", "bmp", "jpg", "jpeg"}
PREVIEW_MAX_SIZE = 64 << 20
PREVIEW_INTERVAL = 0.5      # segundos entre actualizaciones de la vista previa
ALL_TYPES = "All"
INT_TYPES = [("INT files", "*.int"), ("All", "*.*")]
CATEGORY_LABELS = [("images", "Images"), ("audio", "Audio"), ("video", "Video"),
                   ("scripts", "Scripts"), ("data", "Data")]


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


def checkerboard(size, cell=8) -> Image.Image:
    """Fondo de cuadros para ver la transparencia."""
    w, h = size
    img = Image.new('RGBA', size, (204, 204, 204, 255))
    dark = Image.new('RGBA', (cell, cell), (153, 153, 153, 255))
    for y in range(0, h, cell):
        for x in range((y // cell) % 2 * cell, w, cell * 2):
            img.paste(dark, (x, y))
    return img


def preview_image(img: Image.Image) -> ctk.CTkImage:
    img = img.convert('RGBA')
    img.thumbnail(PREVIEW_SIZE)
    bg = checkerboard(img.size)
    bg.alpha_composite(img)
    return ctk.CTkImage(light_image=bg, dark_image=bg, size=bg.size)


def clear_image(label: ctk.CTkLabel, text: str = ""):
    """
    Quita la imagen de un CTkLabel. configure(image=None) no la quita del label
    interno de Tk: este sigue apuntando a la imagen anterior, que se borra al
    liberarse, y el siguiente configure falla con "image ... does not exist".
    """
    label._label.configure(image="")
    label.configure(image=None, text=text)


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

        self.entries = []           # pestaña Archivos INT: lista de cs2_int.IntEntry
        self._int_arc = None        # el .int abierto en esa pestaña
        self._last_preview = 0.0
        self._auto_phrase = ''      # frase detectada (se reemplaza al cambiar de juego)
        self._phrase_origin = ''

        self._build_ui()
        self._update_key_info()
        self._apply_tree_style()
        self.after(50, self._poll_events)

    # ------------------------------------------------------------------ UI

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._build_key_bar()

        self.tabs = ctk.CTkTabview(self)
        self.tabs.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 0))
        self._build_extract_tab(self.tabs.add("Extract"))
        self._build_repack_tab(self.tabs.add("Repack"))
        self._build_text_tab(self.tabs.add("Text"))
        self._build_int_tab(self.tabs.add("INT Files"))
        self._build_status_bar()

    def _build_key_bar(self):
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=16, pady=(10, 0))
        bar.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(bar, text="Passphrase:").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.phrase_var = tk.StringVar()
        self.phrase_var.trace_add("write", lambda *_: self._update_key_info())
        ctk.CTkEntry(bar, textvariable=self.phrase_var, width=200,
                     placeholder_text="detected from the game folder") \
            .grid(row=0, column=1, sticky="w")
        self.key_info = ctk.CTkLabel(bar, text="", anchor="w", text_color=("gray35", "gray65"))
        self.key_info.grid(row=0, column=2, sticky="ew", padx=(10, 0))

    def _path_row(self, tab, row, label, var, command, button="Browse…"):
        """Fila etiqueta + campo + botón."""
        ctk.CTkLabel(tab, text=label).grid(row=row, column=0, sticky="w", padx=(4, 8), pady=6)
        ctk.CTkEntry(tab, textvariable=var).grid(row=row, column=1, sticky="ew", pady=6)
        btn = ctk.CTkButton(tab, text=button, width=120, command=command)
        btn.grid(row=row, column=2, padx=(8, 4), pady=6)
        self.busy_widgets.append(btn)

    def _note(self, tab, row, text):
        ctk.CTkLabel(tab, text=text, justify="left", anchor="w", text_color=("gray35", "gray65")) \
            .grid(row=row, column=1, columnspan=2, sticky="ew", pady=(0, 6))

    def _build_extract_tab(self, tab):
        tab.grid_columnconfigure(1, weight=1)
        tab.grid_rowconfigure(6, weight=1)

        self.src_var = tk.StringVar()
        ctk.CTkLabel(tab, text="Game folder or .int:").grid(row=0, column=0, sticky="w",
                                                                 padx=(4, 8), pady=6)
        src_entry = ctk.CTkEntry(tab, textvariable=self.src_var)
        src_entry.grid(row=0, column=1, sticky="ew", pady=6)
        src_entry.bind("<Return>", lambda _e: self._update_src_info())
        src_entry.bind("<FocusOut>", lambda _e: self._update_src_info())
        btns = ctk.CTkFrame(tab, fg_color="transparent")
        btns.grid(row=0, column=2, padx=(8, 4), pady=6)
        b1 = ctk.CTkButton(btns, text="Folder…", width=80, command=self.browse_src_dir)
        b1.pack(side="left")
        b2 = ctk.CTkButton(btns, text=".int…", width=60, command=self.browse_src_int)
        b2.pack(side="left", padx=(6, 0))
        self.busy_widgets += [b1, b2]

        self.src_info = ctk.CTkLabel(tab, text="", anchor="w", justify="left",
                                     text_color=("gray35", "gray65"))
        self.src_info.grid(row=1, column=1, columnspan=2, sticky="ew")

        self.out_var = tk.StringVar()
        self._path_row(tab, 2, "Output:", self.out_var, lambda: self._ask_dir(self.out_var, "Output folder"))

        cats = ctk.CTkFrame(tab, fg_color="transparent")
        cats.grid(row=3, column=1, columnspan=2, sticky="ew", pady=6)
        self.cat_vars = {}
        for key, label in CATEGORY_LABELS:
            var = tk.BooleanVar(value=True)
            cb = ctk.CTkCheckBox(cats, text=label, variable=var, width=90)
            cb.pack(side="left", padx=(0, 10))
            self.cat_vars[key] = var
            self.busy_widgets.append(cb)

        opts = ctk.CTkFrame(tab, fg_color="transparent")
        opts.grid(row=4, column=1, columnspan=2, sticky="ew", pady=6)
        self.resume_var = tk.BooleanVar(value=True)
        self.convert_var = tk.BooleanVar(value=True)
        for text, var in (("Resume", self.resume_var), ("HG-3 images as PNG", self.convert_var)):
            cb = ctk.CTkCheckBox(opts, text=text, variable=var)
            cb.pack(side="left", padx=(0, 14))
            self.busy_widgets.append(cb)

        ctk.CTkLabel(tab, text="Each .int goes to its own folder. Images with frames: NAME_00.png, "
                               "NAME_01.png…", anchor="w", text_color=("gray35", "gray65")) \
            .grid(row=5, column=1, sticky="w", pady=(6, 0))
        btn = ctk.CTkButton(tab, text="Extract", width=160, command=self.start_extract)
        btn.grid(row=5, column=2, sticky="e", padx=4, pady=(6, 0))
        self.busy_widgets.append(btn)

        self.preview = ctk.CTkLabel(tab, text="")
        self.preview.grid(row=6, column=0, columnspan=3, sticky="nsew", pady=8)

    def _build_repack_tab(self, tab):
        tab.grid_columnconfigure(1, weight=1)
        self.rp_int_var = tk.StringVar()
        self.rp_dir_var = tk.StringVar()
        self.rp_out_var = tk.StringVar()
        self._path_row(tab, 0, "Original .int:", self.rp_int_var,
                       lambda: self._ask_open(self.rp_int_var, "Original .int file", self._rp_defaults))
        self._path_row(tab, 1, "Extracted folder:", self.rp_dir_var,
                       lambda: self._ask_dir(self.rp_dir_var, "Folder with the extracted files"))
        self._note(tab, 2, "The extraction output folder (or that .int's folder inside it). Only the files you "
                           "edited are\nre-encoded; everything else is copied from the original.")
        self._path_row(tab, 3, "Output .int:", self.rp_out_var,
                       lambda: self._ask_save(self.rp_out_var, "Save .int"), "Save as…")
        self.rp_add_var = tk.BooleanVar(value=False)
        cb = ctk.CTkCheckBox(tab, text="Also add new files from the folder", variable=self.rp_add_var)
        cb.grid(row=4, column=1, sticky="w", pady=4)
        self.busy_widgets.append(cb)
        self._note(tab, 5, "The output is a complete .int, encrypted like the original. Replace the game's "
                           ".int with it\n(keep a copy of the original). Edited images must keep their size.")
        btn = ctk.CTkButton(tab, text="Create .int", width=160, command=self.start_repack)
        btn.grid(row=6, column=2, sticky="e", padx=4, pady=(18, 0))
        self.busy_widgets.append(btn)

    def _build_text_tab(self, tab):
        tab.grid_columnconfigure(1, weight=1)
        tab.grid_rowconfigure(8, weight=1)
        self.tx_int_var = tk.StringVar()
        self.tx_dir_var = tk.StringVar()
        self.charmap_var = tk.StringVar()
        self.tx_out_var = tk.StringVar()
        self._path_row(tab, 0, "scene.int:", self.tx_int_var,
                       lambda: self._ask_open(self.tx_int_var, "scene.int", self._tx_defaults))
        self._path_row(tab, 1, "Text folder:", self.tx_dir_var,
                       lambda: self._ask_dir(self.tx_dir_var, "Text folder"))
        self._path_row(tab, 2, "Character table:", self.charmap_var,
                       lambda: self._ask_open(self.charmap_var, "Character table (optional)",
                                              types=[("Text", "*.txt"), ("All", "*.*")]))
        self._note(tab, 3, "Optional: 'á=ｱ' pairs for a custom font, for letters that Shift-JIS lacks.")

        row = ctk.CTkFrame(tab, fg_color="transparent")
        row.grid(row=4, column=1, columnspan=2, sticky="ew", pady=6)
        self.json_var = tk.BooleanVar(value=False)
        cb = ctk.CTkCheckBox(row, text="JSON (with the voice of each line)", variable=self.json_var)
        cb.pack(side="left")
        btn = ctk.CTkButton(row, text="Extract text", width=140, command=self.start_text)
        btn.pack(side="right", padx=(8, 4))
        self.busy_widgets += [cb, btn]

        self._path_row(tab, 5, "Output .int:", self.tx_out_var,
                       lambda: self._ask_save(self.tx_out_var, "Save .int"), "Save as…")
        row = ctk.CTkFrame(tab, fg_color="transparent")
        row.grid(row=6, column=1, columnspan=2, sticky="ew", pady=6)
        ctk.CTkLabel(row, text="Lines can be any length. Scripts without changes are copied unchanged.",
                     text_color=("gray35", "gray65")).pack(side="left")
        btn = ctk.CTkButton(row, text="Rebuild", width=140, command=self.start_text_rebuild)
        btn.pack(side="right", padx=(8, 4))
        self.busy_widgets.append(btn)

        self.report_box = ctk.CTkTextbox(tab, height=120, wrap="none")
        self.report_box.grid(row=8, column=0, columnspan=3, sticky="nsew", padx=4, pady=(8, 0))
        self.report_box.configure(state="disabled")

    def _build_int_tab(self, tab):
        tab.grid_columnconfigure(1, weight=1)
        tab.grid_rowconfigure(3, weight=1)

        self.int_var = tk.StringVar()
        ctk.CTkLabel(tab, text=".int file:").grid(row=0, column=0, sticky="w", padx=(4, 8), pady=4)
        int_entry = ctk.CTkEntry(tab, textvariable=self.int_var)
        int_entry.grid(row=0, column=1, sticky="ew", pady=4)
        int_entry.bind("<Return>", lambda _e: self.load_int(self.int_var.get()))
        btn = ctk.CTkButton(tab, text="Open…", width=100, command=self.browse_int)
        btn.grid(row=0, column=2, padx=(8, 4), pady=4)
        self.busy_widgets.append(btn)

        self.int_info = ctk.CTkLabel(tab, text="No file open", anchor="w", text_color=("gray35", "gray65"))
        self.int_info.grid(row=1, column=0, columnspan=3, sticky="ew", padx=4)

        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *_: self._refresh_tree())
        ctk.CTkLabel(tab, text="Filter:").grid(row=2, column=0, sticky="w", padx=(4, 8), pady=4)
        filt = ctk.CTkFrame(tab, fg_color="transparent")
        filt.grid(row=2, column=1, columnspan=2, sticky="ew", padx=(0, 4), pady=4)
        filt.grid_columnconfigure(0, weight=1)
        ctk.CTkEntry(filt, textvariable=self.filter_var, placeholder_text="name contains…") \
            .grid(row=0, column=0, sticky="ew")
        self.type_var = tk.StringVar(value=ALL_TYPES)
        self.type_menu = ctk.CTkOptionMenu(filt, values=[ALL_TYPES], width=100, variable=self.type_var,
                                           command=lambda _v: self._refresh_tree())
        self.type_menu.grid(row=0, column=1, padx=(8, 0))

        body = ctk.CTkFrame(tab, fg_color="transparent")
        body.grid(row=3, column=0, columnspan=3, sticky="nsew", padx=4, pady=4)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)
        tree_frame = ctk.CTkFrame(body, fg_color="transparent")
        tree_frame.grid(row=0, column=0, sticky="nsew")
        tree_frame.grid_columnconfigure(0, weight=1)
        tree_frame.grid_rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(tree_frame, columns=("type", "size"), selectmode="extended",
                                 style="CS2.Treeview")
        self.tree.heading("#0", text="Name", anchor="w")
        self.tree.heading("type", text="Type", anchor="w")
        self.tree.heading("size", text="Size", anchor="e")
        self.tree.column("#0", stretch=True, width=300)
        self.tree.column("type", stretch=False, width=60, anchor="w")
        self.tree.column("size", stretch=False, width=90, anchor="e")
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ctk.CTkScrollbar(tree_frame, command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._on_tree_select())
        # ← → cambian de fotograma en la vista previa
        self.tree.bind("<Left>", lambda _e: (self._step_frame(-1), "break")[1])
        self.tree.bind("<Right>", lambda _e: (self._step_frame(1), "break")[1])
        self._build_preview(body).grid(row=0, column=1, sticky="ns", padx=(8, 0))

        self.raw_out_var = tk.StringVar()
        ctk.CTkLabel(tab, text="Output:").grid(row=4, column=0, sticky="w", padx=(4, 8), pady=4)
        ctk.CTkEntry(tab, textvariable=self.raw_out_var).grid(row=4, column=1, sticky="ew", pady=4)
        btn = ctk.CTkButton(tab, text="Browse…", width=100,
                            command=lambda: self._ask_dir(self.raw_out_var, "Output folder"))
        btn.grid(row=4, column=2, padx=(8, 4), pady=4)
        self.busy_widgets.append(btn)

        actions = ctk.CTkFrame(tab, fg_color="transparent")
        actions.grid(row=5, column=0, columnspan=3, sticky="ew", padx=4, pady=(4, 0))
        self.raw_png_var = tk.BooleanVar(value=True)
        cb = ctk.CTkCheckBox(actions, text="HG-3 as PNG", variable=self.raw_png_var)
        cb.pack(side="left")
        self.busy_widgets.append(cb)
        self.sel_label = ctk.CTkLabel(actions, text="", text_color=("gray35", "gray65"))
        self.sel_label.pack(side="left", padx=12)
        btn_all = ctk.CTkButton(actions, text="Extract all", command=lambda: self.start_raw(False))
        btn_all.pack(side="right")
        btn_sel = ctk.CTkButton(actions, text="Extract selected", command=lambda: self.start_raw(True))
        btn_sel.pack(side="right", padx=8)
        btn_pack = ctk.CTkButton(actions, text="Pack folder…", fg_color="gray40",
                                 hover_color="gray30", command=self.start_pack)
        btn_pack.pack(side="right", padx=(0, 8))
        self.busy_widgets += [btn_all, btn_sel, btn_pack]

        ctk.CTkLabel(tab, text="Files are saved decrypted; without \"HG-3 as PNG\", images stay as .hg3.",
                     anchor="w", text_color=("gray35", "gray65")) \
            .grid(row=6, column=0, columnspan=3, sticky="ew", padx=4, pady=(4, 0))

    def _build_preview(self, master):
        """Panel de vista previa: imagen, fotogramas (◀ ▶), 'en el lienzo' e información."""
        frame = ctk.CTkFrame(master, width=PANEL_SIZE[0] + 20)
        frame.grid_propagate(False)
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(0, weight=1)
        self.pv_image = ctk.CTkLabel(frame, text="No preview")
        self.pv_image.grid(row=0, column=0, sticky="nsew", padx=10, pady=(10, 4))

        nav = ctk.CTkFrame(frame, fg_color="transparent")
        nav.grid(row=1, column=0, sticky="ew", padx=10)
        nav.grid_columnconfigure(1, weight=1)
        self.pv_prev = ctk.CTkButton(nav, text="◀", width=36, command=lambda: self._step_frame(-1))
        self.pv_prev.grid(row=0, column=0)
        self.pv_frame_label = ctk.CTkLabel(nav, text="")
        self.pv_frame_label.grid(row=0, column=1)
        self.pv_next = ctk.CTkButton(nav, text="▶", width=36, command=lambda: self._step_frame(1))
        self.pv_next.grid(row=0, column=2)
        self.pv_canvas_var = tk.BooleanVar(value=False)
        self.pv_canvas = ctk.CTkCheckBox(nav, text="On canvas", variable=self.pv_canvas_var,
                                         command=self._render_preview)
        self.pv_canvas.grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))

        self.pv_info = ctk.CTkLabel(frame, text="", justify="left", anchor="w")
        self.pv_info.grid(row=2, column=0, sticky="ew", padx=10, pady=(6, 10))
        self._pv_frames = []        # [(imagen, Hg3Frame)] de la entrada seleccionada
        self._pv_index = 0
        self._pv_name = ''
        self._pv_ref = None         # CTkImage mostrada (para que no se libere)
        self._set_nav(False)
        return frame

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

        theme = ctk.CTkOptionMenu(bar, values=["System", "Light", "Dark"], width=100,
                                  command=self._change_theme)
        theme.grid(row=0, column=3, rowspan=2, padx=(0, 10))

    # --------------------------------------------------------------- Tema

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

    # ------------------------------------------------------------ Clave

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
        origin = f"from {self._phrase_origin}   ·   " if phrase == self._auto_phrase else ""
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

    # ------------------------------------------------- Diálogos comunes

    def _ask_open(self, var, title, then=None, types=None):
        path = filedialog.askopenfilename(title=title, filetypes=types or INT_TYPES)
        if path:
            var.set(path)
            if then:
                then(path)

    def _ask_dir(self, var, title):
        path = filedialog.askdirectory(title=title)
        if path:
            var.set(path)

    def _ask_save(self, var, title):
        initial = Path(var.get() or "scene.int")
        path = filedialog.asksaveasfilename(title=title, defaultextension=".int",
                                            initialdir=str(initial.parent) if initial.parent.exists() else None,
                                            initialfile=initial.name, filetypes=INT_TYPES)
        if path:
            var.set(path)

    def _confirm_overwrite(self, out_path, *sources) -> bool:
        out = Path(out_path).resolve()
        if any(out == Path(s).resolve() for s in sources if s):
            messagebox.showwarning(APP_TITLE, "The output cannot be the original file.")
            return False
        return not out.exists() or messagebox.askyesno(APP_TITLE, f"{out.name} already exists.\nOverwrite it?")

    @staticmethod
    def _new_path(int_path) -> str:
        """Salida por defecto: <carpeta del juego>_new/<nombre>.int, junto al juego."""
        p = Path(int_path)
        return str(p.parent.parent / f"{p.parent.name}_new" / p.name)

    def _rp_defaults(self, path):
        self._detect_phrase(path)
        self.rp_out_var.set(self._new_path(path))
        if not self.rp_dir_var.get() and self.out_var.get():
            self.rp_dir_var.set(self.out_var.get())

    def _tx_defaults(self, path):
        self._detect_phrase(path)
        game = Path(path).parent
        if not self.tx_dir_var.get():
            self.tx_dir_var.set(str(game.parent / f"{game.name}_text"))
        if not self.tx_out_var.get():
            self.tx_out_var.set(self._new_path(path))

    # ------------------------------------------------------ Pestaña Extraer

    def browse_src_dir(self):
        path = filedialog.askdirectory(title="Game folder")
        if path:
            self._set_src(path)

    def browse_src_int(self):
        path = filedialog.askopenfilename(title="Open .int file", filetypes=INT_TYPES)
        if path:
            self._set_src(path)

    def _set_src(self, path):
        self.src_var.set(path)
        if not self.out_var.get():
            # Junto a la carpeta del juego, nunca dentro
            p = Path(path)
            game_dir = p.parent if p.is_file() else p
            self.out_var.set(str(game_dir.parent / f"{game_dir.name}_extracted"))
        self._update_src_info()

    def _update_src_info(self):
        src = self.src_var.get().strip().strip('"')
        if not src or not Path(src).exists():
            self.src_info.configure(text="")
            return
        self._detect_phrase(src)
        try:
            ints = cs2_extract.find_ints(src)
        except OSError as e:
            self.src_info.configure(text=str(e))
            return
        total = sum(p.stat().st_size for p in ints)
        names = ", ".join(p.stem for p in ints) or "no .int files"
        self.src_info.configure(text=f"{len(ints)} .int ({format_size(total)}): {names}")

    def start_extract(self):
        src = self.src_var.get().strip().strip('"')
        out_dir = self.out_var.get().strip()
        cats = [k for k, v in self.cat_vars.items() if v.get()]
        if not src or not Path(src).exists():
            messagebox.showwarning(APP_TITLE, "Choose the game folder or a .int file.")
            return
        if not out_dir:
            messagebox.showwarning(APP_TITLE, "Choose an output folder.")
            return
        if not cats:
            messagebox.showwarning(APP_TITLE, "Choose at least one category.")
            return
        if not cs2_extract.find_ints(src):
            messagebox.showwarning(APP_TITLE, "There are no .int files to process.")
            return

        def done(r):
            text = f"{r.files} files ({r.converted} images converted to PNG) in:\n{out_dir}"
            if r.skipped:
                text += f"\n({r.skipped} were already done)"
            if r.warnings:
                text += f"\n\n{len(r.warnings)} warnings:\n" + "\n".join(r.warnings[:5])
            if r.errors:
                text += f"\n\n{len(r.errors)} errors:\n" + "\n".join(r.errors[:10])
            if messagebox.askyesno(APP_TITLE, text + "\n\nOpen the folder?"):
                open_folder(out_dir)

        clear_image(self.preview)
        self.run_task(cs2_extract.extract_all, (src, out_dir),
                      {"categories": cats, "convert": self.convert_var.get(), "resume": self.resume_var.get(),
                       "phrase": self._phrase()},
                      "Extracting", done)

    def _maybe_preview(self, name):
        """Muestra el último PNG escrito, como mucho cada PREVIEW_INTERVAL segundos."""
        if not name.lower().endswith(".png") or time.time() - self._last_preview < PREVIEW_INTERVAL:
            return
        self._last_preview = time.time()
        try:
            with Image.open(name) as img:
                img.load()
                self.preview.configure(image=preview_image(img), text="")
        except OSError:
            pass

    # -------------------------------------------------- Pestaña Reempaquetar

    def start_repack(self):
        int_path, folder, out = (v.get().strip() for v in (self.rp_int_var, self.rp_dir_var, self.rp_out_var))
        if not int_path or not Path(int_path).is_file():
            messagebox.showwarning(APP_TITLE, "Choose the original .int.")
            return
        if not folder or not Path(folder).is_dir():
            messagebox.showwarning(APP_TITLE, "Choose the folder with the extracted files.")
            return
        if not out or not self._confirm_overwrite(out, int_path):
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
            if messagebox.askyesno(APP_TITLE, f"{len(r.changed)} files replaced, {len(r.added)} added:\n"
                                              f"{listing}\n\n{out} ({r.entries} files)\n\nOpen the folder?"):
                open_folder(Path(out).parent)

        self.run_task(cs2_extract.repack, (int_path, folder, out),
                      {"add_new": self.rp_add_var.get(), "phrase": self._phrase()}, "Repacking", done)

    # ------------------------------------------------------ Pestaña Texto

    def _load_charmap(self):
        path = self.charmap_var.get().strip()
        return CharMap.load(path) if path else None

    def _show_report(self, text):
        self.report_box.configure(state="normal")
        self.report_box.delete("1.0", "end")
        self.report_box.insert("1.0", text)
        self.report_box.configure(state="disabled")

    def start_text(self):
        int_path, folder = (v.get().strip() for v in (self.tx_int_var, self.tx_dir_var))
        if not int_path or not Path(int_path).is_file():
            messagebox.showwarning(APP_TITLE, "Choose scene.int.")
            return
        if not folder:
            messagebox.showwarning(APP_TITLE, "Choose the text folder.")
            return
        try:
            charmap = self._load_charmap()
            key = self._key_for(int_path)
        except Exception as e:
            messagebox.showerror(APP_TITLE, str(e))
            return

        def done(r):
            lines = [f"{r.lines} lines from {r.files} scripts in {folder}",
                     "One file per script: LINE|TEXT. Translate only the text after '|' and keep the codes "
                     "(\\n, \\@, [ ])."]
            lines += [f"[WARNING] {w}" for w in r.warnings]
            self._show_report("\n".join(lines))
            if messagebox.askyesno(APP_TITLE, f"{r.lines} lines from {r.files} scripts.\n\nOpen the folder?"):
                open_folder(folder)

        self.run_task(cs2_script.extract_text, (int_path, folder),
                      {"key": key, "charmap": charmap, "with_json": self.json_var.get()},
                      "Extracting text", done)

    def start_text_rebuild(self):
        int_path, folder, out = (v.get().strip() for v in (self.tx_int_var, self.tx_dir_var, self.tx_out_var))
        if not int_path or not Path(int_path).is_file():
            messagebox.showwarning(APP_TITLE, "Choose scene.int.")
            return
        if not folder or not Path(folder).is_dir():
            messagebox.showwarning(APP_TITLE, "Choose the text folder.")
            return
        if not out or not self._confirm_overwrite(out, int_path):
            return
        try:
            charmap = self._load_charmap()
            key = self._key_for(int_path)
        except Exception as e:
            messagebox.showerror(APP_TITLE, str(e))
            return

        def done(rep):
            lines = [rep.summary()]
            if rep.output_entries:
                lines.append(f"Output: {out} ({rep.output_entries} files)")
            lines.append("")
            lines += [f"[WARNING] {w}" for w in rep.warnings]
            lines += [f"[{', '.join(e.flags)}] {e.entry} {e.key}: {e.detail}" for e in rep.entries]
            self._show_report("\n".join(lines))

        self.run_task(cs2_script.rebuild, (int_path, folder, out),
                      {"key": key, "charmap": charmap}, "Rebuilding", done)

    # -------------------------------------------------- Pestaña Archivos INT

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
        self._int_arc = arc         # abierto mientras se muestra, para la vista previa
        entries, encrypted = arc.entries, arc.encrypted
        self.int_var.set(path)
        self.entries = entries
        p = Path(path)
        self.raw_out_var.set(str(p.parent.parent / f"{p.parent.name}_{p.stem}"))
        types = Counter(e.ext or '(none)' for e in entries)
        total = sum(e.size for e in entries)
        self.int_info.configure(text=f"{len(entries)} files   ·   {format_size(total)}   ·   "
                                     f"{'encrypted' if encrypted else 'not encrypted'}   ·   "
                                     + ", ".join(f"{n} {ext}" for ext, n in types.most_common()))
        self.type_menu.configure(values=[ALL_TYPES] + sorted(types))
        self.type_var.set(ALL_TYPES)
        self._refresh_tree()
        self._pv_frames = []
        self._set_preview(None, "")

    def _refresh_tree(self):
        self.tree.delete(*self.tree.get_children())
        text = self.filter_var.get().lower()
        ext = self.type_var.get()
        for i, e in enumerate(self.entries):
            if text in e.name.lower() and ext in (ALL_TYPES, e.ext or '(none)'):
                self.tree.insert("", "end", iid=str(i), text=e.name,
                                 values=(e.ext, format_size(e.size)))
        self._update_selection_label()

    # Vista previa de la entrada seleccionada

    def _on_tree_select(self):
        self._update_selection_label()
        sel = self.tree.selection()
        if len(sel) == 1:
            self._show_entry(self.entries[int(sel[0])])

    def _show_entry(self, e):
        self._pv_frames, self._pv_index, self._pv_name = [], 0, e.name
        info = f"{e.name}\n{e.ext.upper() or 'File'} · {format_size(e.size)}"
        if e.ext not in PREVIEW_TYPES or e.size > PREVIEW_MAX_SIZE:
            self._set_preview(None, info)
            return
        try:
            data = self._int_arc.read(e)
            if cs2_hg3.is_hg3(data):
                self._pv_frames = cs2_hg3.decode(data)
                self._render_preview()
                return
            with Image.open(io.BytesIO(data)) as img:
                img.load()
                self._pv_frames = [(img.copy(), None)]
            self._render_preview()
        except Exception as ex:
            self._set_preview(None, f"{info}\n\nCould not show it:\n{ex}")

    def _render_preview(self):
        if not self._pv_frames:
            return
        img, fr = self._pv_frames[self._pv_index]
        count = len(self._pv_frames)
        lines = [self._pv_name]
        if fr is None:
            lines.append(f"{img.width}×{img.height} {img.mode}")
        else:
            lines.append(f"{fr.width}×{fr.height}, {fr.bpp}-bit")
            lines.append(f"Position: x={fr.x}  y={fr.y}")
            lines.append(f"Canvas: {fr.canvas_w}×{fr.canvas_h}")
            if count > 1:
                names = cs2_hg3.frame_names(Path(self._pv_name).stem, count)
                lines.append(f"Saved as {names[self._pv_index]}")
            if self.pv_canvas_var.get() and fr.canvas_w and fr.canvas_h:
                # El fotograma en su posición dentro del lienzo, con el borde del lienzo marcado
                canvas = Image.new('RGBA', (fr.canvas_w, fr.canvas_h), (0, 0, 0, 0))
                canvas.alpha_composite(img.convert('RGBA'), (fr.x, fr.y))
                img = canvas
        self.pv_frame_label.configure(text=f"Frame {self._pv_index + 1} / {count}")
        self._set_nav(fr is not None, count)
        self._set_preview(img, "\n".join(lines))

    def _step_frame(self, delta):
        if self._pv_frames:
            self._pv_index = (self._pv_index + delta) % len(self._pv_frames)
            self._render_preview()

    def _set_nav(self, is_hg3, count=0):
        many = "normal" if count > 1 else "disabled"
        self.pv_prev.configure(state=many)
        self.pv_next.configure(state=many)
        self.pv_canvas.configure(state="normal" if is_hg3 else "disabled")
        if not is_hg3:
            self.pv_frame_label.configure(text="")

    def _set_preview(self, img, text):
        if img is None:
            self._pv_ref = None
            clear_image(self.pv_image, "No preview")
            self._set_nav(False)
        else:
            img = img.convert('RGBA')
            img.thumbnail(PANEL_SIZE)
            bg = checkerboard(img.size)
            bg.alpha_composite(img)
            self._pv_ref = ctk.CTkImage(light_image=bg, dark_image=bg, size=bg.size)
            self.pv_image.configure(image=self._pv_ref, text="")
        self.pv_info.configure(text=text)

    def _update_selection_label(self):
        if not self.entries:
            self.sel_label.configure(text="")
            return
        self.sel_label.configure(text=f"{len(self.tree.get_children())} shown · "
                                      f"{len(self.tree.selection())} selected")

    def start_raw(self, only_selected):
        int_path = self.int_var.get().strip()
        out_dir = self.raw_out_var.get().strip()
        if not self.entries:
            messagebox.showwarning(APP_TITLE, "Open a .int file first.")
            return
        if not out_dir:
            messagebox.showwarning(APP_TITLE, "Choose an output folder.")
            return
        names = None
        if only_selected:
            names = [self.entries[int(iid)].name for iid in self.tree.selection()]
            if not names:
                messagebox.showwarning(APP_TITLE, "No files selected.")
                return
        try:
            key = self._key_for(int_path)
        except Exception as e:
            messagebox.showerror(APP_TITLE, str(e))
            return

        def done(count):
            if messagebox.askyesno(APP_TITLE, f"Saved {count} files to:\n{out_dir}\n\nOpen the folder?"):
                open_folder(out_dir)

        self.run_task(cs2_extract.extract_entries, (int_path, out_dir),
                      {"names": names, "key": key, "convert": self.raw_png_var.get()}, "Extracting", done)

    def start_pack(self):
        folder = filedialog.askdirectory(title="Folder with the files to pack")
        if not folder:
            return
        out = filedialog.asksaveasfilename(title="Save .int", defaultextension=".int",
                                           initialfile=f"{Path(folder).name}.int", filetypes=INT_TYPES)
        if not out:
            return
        like = self.int_var.get().strip() if self.entries else None
        if like and not messagebox.askyesno(APP_TITLE, f"Keep the order and encryption of\n{Path(like).name}?\n\n"
                                                       "(No: the .int is not encrypted)"):
            like = None
        try:
            key = self._key_for(like) if like else None
        except Exception as e:
            messagebox.showerror(APP_TITLE, str(e))
            return

        def done(count):
            if messagebox.askyesno(APP_TITLE, f"Archive created ({count} files):\n{out}\n\nOpen the folder?"):
                open_folder(Path(out).parent)

        self.run_task(cs2_int.pack, (folder, out), {"like": like, "key": key}, "Packing", done)

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
    app = App()
    app.mainloop()


if __name__ == '__main__':
    multiprocessing.freeze_support()
    main()
