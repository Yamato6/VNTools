#!/usr/bin/env python3
"""
NeXAS Tools GUI - Interfaz gráfica para los archivos .pac del motor NeXAS (Giga)
Juego: Maji de Watashi ni Koi Shinasai! S (Minato Soft, 2012)
"""

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

APP_TITLE = "NeXAS Tools"
PREVIEW_SIZE = (360, 270)
PREVIEW_INTERVAL = 0.5      # segundos entre actualizaciones de la vista previa
ALL_TYPES = "All"
PAC_TYPES = [("PAC files", "*.pac"), ("All", "*.*")]
CATEGORY_LABELS = [("images", "Images"), ("audio", "Audio"), ("video", "Video"),
                   ("scripts", "Scripts"), ("data", "Data tables")]


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
        self.geometry("900x660")
        self.minsize(720, 540)

        self.events = queue.Queue()
        self.cancel_event = threading.Event()
        self.worker = None
        self.busy_widgets = []

        self.entries = []           # pestaña Archivos PAC: lista de nexas_pac.PacEntry
        self._last_preview = 0.0

        self._build_ui()
        self._apply_tree_style()
        self.after(50, self._poll_events)

    # ------------------------------------------------------------------ UI

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.tabs = ctk.CTkTabview(self)
        self.tabs.grid(row=0, column=0, sticky="nsew", padx=12, pady=(6, 0))
        self._build_extract_tab(self.tabs.add("Extract"))
        self._build_repack_tab(self.tabs.add("Repack"))
        self._build_text_tab(self.tabs.add("Text"))
        self._build_pac_tab(self.tabs.add("PAC Files"))
        self._build_status_bar()

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
        ctk.CTkLabel(tab, text="Game folder or .pac:").grid(row=0, column=0, sticky="w",
                                                                 padx=(4, 8), pady=6)
        src_entry = ctk.CTkEntry(tab, textvariable=self.src_var)
        src_entry.grid(row=0, column=1, sticky="ew", pady=6)
        src_entry.bind("<Return>", lambda _e: self._update_src_info())
        src_entry.bind("<FocusOut>", lambda _e: self._update_src_info())
        btns = ctk.CTkFrame(tab, fg_color="transparent")
        btns.grid(row=0, column=2, padx=(8, 4), pady=6)
        b1 = ctk.CTkButton(btns, text="Folder…", width=80, command=self.browse_src_dir)
        b1.pack(side="left")
        b2 = ctk.CTkButton(btns, text=".pac…", width=60, command=self.browse_src_pac)
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
        self.extras_var = tk.BooleanVar(value=True)
        self.sprites_var = tk.BooleanVar(value=False)
        for text, var in (("Resume", self.resume_var), ("FIL masks as PNG", self.convert_var),
                          ("Extras (NMV frames, JSON, CSV)", self.extras_var),
                          ("Compose sprites", self.sprites_var)):
            cb = ctk.CTkCheckBox(opts, text=text, variable=var)
            cb.pack(side="left", padx=(0, 14))
            self.busy_widgets.append(cb)

        ctk.CTkLabel(tab, text="Each .pac goes to its own folder. Extras are for viewing only; repacking "
                               "ignores them.", anchor="w", text_color=("gray35", "gray65")) \
            .grid(row=5, column=1, sticky="w", pady=(6, 0))
        btn = ctk.CTkButton(tab, text="Extract", width=160, command=self.start_extract)
        btn.grid(row=5, column=2, sticky="e", padx=4, pady=(6, 0))
        self.busy_widgets.append(btn)

        self.preview = ctk.CTkLabel(tab, text="")
        self.preview.grid(row=6, column=0, columnspan=3, sticky="nsew", pady=8)

    def _build_repack_tab(self, tab):
        tab.grid_columnconfigure(1, weight=1)
        self.rp_pac_var = tk.StringVar()
        self.rp_dir_var = tk.StringVar()
        self.rp_out_var = tk.StringVar()
        self._path_row(tab, 0, "Original .pac:", self.rp_pac_var,
                       lambda: self._ask_open(self.rp_pac_var, "Original .pac file", self._rp_defaults))
        self._path_row(tab, 1, "Extracted folder:", self.rp_dir_var,
                       lambda: self._ask_dir(self.rp_dir_var, "Folder with the extracted files"))
        self._note(tab, 2, "The extraction output folder (or that .pac's folder inside it). Only the files you "
                           "edited are\nre-encoded; everything else is copied from the original.")
        self._path_row(tab, 3, "Output .pac:", self.rp_out_var,
                       lambda: self._ask_save(self.rp_out_var, "Save .pac"), "Save as…")
        self.rp_patch_var = tk.BooleanVar(value=True)
        self.rp_add_var = tk.BooleanVar(value=False)
        self.resize_var = tk.BooleanVar(value=False)
        row = 4
        for text, var in (("Only the changed files (a patch to save as Update4.pac … Update8.pac)", self.rp_patch_var),
                          ("Also add new files from the folder", self.rp_add_var),
                          ("Allow images with a different size than the original", self.resize_var)):
            cb = ctk.CTkCheckBox(tab, text=text, variable=var, command=self._rp_patch_changed)
            cb.grid(row=row, column=1, sticky="w", pady=4)
            self.busy_widgets.append(cb)
            row += 1
        self._note(tab, row, "The game loads Update8.pac first and Update.pac last, before the base archives:\n"
                             "a patch overrides the files it contains without touching the game files.")
        btn = ctk.CTkButton(tab, text="Create .pac", width=160, command=self.start_repack)
        btn.grid(row=row + 1, column=2, sticky="e", padx=4, pady=(18, 0))
        self.busy_widgets.append(btn)

    def _build_text_tab(self, tab):
        tab.grid_columnconfigure(1, weight=1)
        tab.grid_rowconfigure(9, weight=1)
        self.tx_pac_var = tk.StringVar()
        self.tx_ref_var = tk.StringVar()
        self.tx_dir_var = tk.StringVar()
        self.charmap_var = tk.StringVar()
        self.tx_out_var = tk.StringVar()
        self._path_row(tab, 0, "Script .pac:", self.tx_pac_var,
                       lambda: self._ask_open(self.tx_pac_var, "Script.pac, Update3.pac or Config.pac",
                                              self._tx_defaults))
        self._path_row(tab, 1, "Reference .pac:", self.tx_ref_var,
                       lambda: self._ask_open(self.tx_ref_var, "Reference .pac (optional)"))
        self._note(tab, 2, "Optional: the same scripts in another language (e.g. Update3.pac, English). Each "
                           "line gets\nthe reference text as a '#>' comment.")
        self._path_row(tab, 3, "Text folder:", self.tx_dir_var,
                       lambda: self._ask_dir(self.tx_dir_var, "Text folder"))
        self._path_row(tab, 4, "Character table:", self.charmap_var,
                       lambda: self._ask_open(self.charmap_var, "Character table (optional)",
                                              types=[("Text", "*.txt"), ("All", "*.*")]))

        row = ctk.CTkFrame(tab, fg_color="transparent")
        row.grid(row=5, column=1, columnspan=2, sticky="ew", pady=6)
        self.listing_var = tk.BooleanVar(value=False)
        self.json_var = tk.BooleanVar(value=False)
        cb1 = ctk.CTkCheckBox(row, text="Script listing", variable=self.listing_var)
        cb1.pack(side="left")
        cb2 = ctk.CTkCheckBox(row, text="JSON", variable=self.json_var)
        cb2.pack(side="left", padx=(14, 0))
        btn = ctk.CTkButton(row, text="Extract text", width=140, command=self.start_text)
        btn.pack(side="right", padx=(8, 4))
        self.busy_widgets += [cb1, cb2, btn]

        self._path_row(tab, 6, "Output .pac:", self.tx_out_var,
                       lambda: self._ask_save(self.tx_out_var, "Save .pac"), "Save as…")
        row = ctk.CTkFrame(tab, fg_color="transparent")
        row.grid(row=7, column=1, columnspan=2, sticky="ew", pady=6)
        self.tx_patch_var = tk.BooleanVar(value=True)
        cb = ctk.CTkCheckBox(row, text="Only the changed files (patch)", variable=self.tx_patch_var)
        cb.pack(side="left")
        btn = ctk.CTkButton(row, text="Rebuild", width=140, command=self.start_text_rebuild)
        btn.pack(side="right", padx=(8, 4))
        self.busy_widgets += [cb, btn]

        self.report_box = ctk.CTkTextbox(tab, height=120, wrap="none")
        self.report_box.grid(row=9, column=0, columnspan=3, sticky="nsew", padx=4, pady=(8, 0))
        self.report_box.configure(state="disabled")

    def _build_pac_tab(self, tab):
        tab.grid_columnconfigure(1, weight=1)
        tab.grid_rowconfigure(3, weight=1)

        self.pac_var = tk.StringVar()
        ctk.CTkLabel(tab, text=".pac file:").grid(row=0, column=0, sticky="w", padx=(4, 8), pady=4)
        pac_entry = ctk.CTkEntry(tab, textvariable=self.pac_var)
        pac_entry.grid(row=0, column=1, sticky="ew", pady=4)
        pac_entry.bind("<Return>", lambda _e: self.load_pac(self.pac_var.get()))
        btn = ctk.CTkButton(tab, text="Open…", width=100, command=self.browse_pac)
        btn.grid(row=0, column=2, padx=(8, 4), pady=4)
        self.busy_widgets.append(btn)

        self.pac_info = ctk.CTkLabel(tab, text="No file open", anchor="w", text_color=("gray35", "gray65"))
        self.pac_info.grid(row=1, column=0, columnspan=3, sticky="ew", padx=4)

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

        tree_frame = ctk.CTkFrame(tab, fg_color="transparent")
        tree_frame.grid(row=3, column=0, columnspan=3, sticky="nsew", padx=4, pady=4)
        tree_frame.grid_columnconfigure(0, weight=1)
        tree_frame.grid_rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(tree_frame, columns=("type", "size"), selectmode="extended",
                                 style="NeXAS.Treeview")
        self.tree.heading("#0", text="Name", anchor="w")
        self.tree.heading("type", text="Type", anchor="w")
        self.tree.heading("size", text="Size", anchor="e")
        self.tree.column("#0", stretch=True, width=400)
        self.tree.column("type", stretch=False, width=80, anchor="w")
        self.tree.column("size", stretch=False, width=110, anchor="e")
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ctk.CTkScrollbar(tree_frame, command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._update_selection_label())

        self.raw_out_var = tk.StringVar()
        ctk.CTkLabel(tab, text="Output:").grid(row=4, column=0, sticky="w", padx=(4, 8), pady=4)
        ctk.CTkEntry(tab, textvariable=self.raw_out_var).grid(row=4, column=1, sticky="ew", pady=4)
        btn = ctk.CTkButton(tab, text="Browse…", width=100,
                            command=lambda: self._ask_dir(self.raw_out_var, "Output folder"))
        btn.grid(row=4, column=2, padx=(8, 4), pady=4)
        self.busy_widgets.append(btn)

        actions = ctk.CTkFrame(tab, fg_color="transparent")
        actions.grid(row=5, column=0, columnspan=3, sticky="ew", padx=4, pady=(4, 0))
        self.sel_label = ctk.CTkLabel(actions, text="", text_color=("gray35", "gray65"))
        self.sel_label.pack(side="left")
        btn_all = ctk.CTkButton(actions, text="Extract all", command=lambda: self.start_raw(False))
        btn_all.pack(side="right")
        btn_sel = ctk.CTkButton(actions, text="Extract selected", command=lambda: self.start_raw(True))
        btn_sel.pack(side="right", padx=8)
        btn_pack = ctk.CTkButton(actions, text="Pack folder…", fg_color="gray40",
                                 hover_color="gray30", command=self.start_pack)
        btn_pack.pack(side="right", padx=(0, 8))
        self.busy_widgets += [btn_all, btn_sel, btn_pack]

        ctk.CTkLabel(tab, text="Files are saved as they are in the .pac (decompressed).",
                     anchor="w", text_color=("gray35", "gray65")) \
            .grid(row=6, column=0, columnspan=3, sticky="ew", padx=4, pady=(4, 0))

    def _build_status_bar(self):
        bar = ctk.CTkFrame(self)
        bar.grid(row=1, column=0, sticky="ew", padx=12, pady=12)
        bar.grid_columnconfigure(1, weight=1)

        self.progress = ctk.CTkProgressBar(bar)
        self.progress.set(0)
        self.progress.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(10, 4))

        self.status = ctk.CTkLabel(bar, text="Ready", anchor="w")
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
        style.configure("NeXAS.Treeview", background=bg, fieldbackground=bg, foreground=fg,
                        rowheight=24, borderwidth=0)
        style.map("NeXAS.Treeview", background=[("selected", "#1f6aa5")], foreground=[("selected", "#ffffff")])
        style.configure("NeXAS.Treeview.Heading", background=head_bg, foreground=fg, relief="flat")
        style.map("NeXAS.Treeview.Heading", background=[("active", head_bg)])

    # ------------------------------------------------- Diálogos comunes

    def _ask_open(self, var, title, then=None, types=None):
        path = filedialog.askopenfilename(title=title, filetypes=types or PAC_TYPES)
        if path:
            var.set(path)
            if then:
                then(path)

    def _ask_dir(self, var, title):
        path = filedialog.askdirectory(title=title)
        if path:
            var.set(path)

    def _ask_save(self, var, title):
        initial = Path(var.get() or "Update4.pac")
        path = filedialog.asksaveasfilename(title=title, defaultextension=".pac",
                                            initialdir=str(initial.parent) if initial.parent.exists() else None,
                                            initialfile=initial.name, filetypes=PAC_TYPES)
        if path:
            var.set(path)

    def _confirm_overwrite(self, out_path, *sources) -> bool:
        out = Path(out_path).resolve()
        if any(out == Path(s).resolve() for s in sources if s):
            messagebox.showwarning(APP_TITLE, "The output cannot be the original file.")
            return False
        return not out.exists() or messagebox.askyesno(APP_TITLE, f"{out.name} already exists.\nOverwrite it?")

    @staticmethod
    def _free_update_name(game: Path) -> str:
        """El primer UpdateN.pac (4..8) que no existe en la carpeta del juego."""
        for n in range(4, 9):
            if not (game / f"Update{n}.pac").exists():
                return f"Update{n}.pac"
        return "Update8.pac"

    def _rp_defaults(self, _path):
        self._rp_patch_changed()
        if not self.rp_dir_var.get() and self.out_var.get():
            self.rp_dir_var.set(self.out_var.get())

    def _rp_patch_changed(self):
        pac = self.rp_pac_var.get().strip()
        if not pac:
            return
        p = Path(pac)
        game = p.parent
        name = self._free_update_name(game) if self.rp_patch_var.get() else p.name
        self.rp_out_var.set(str(game.parent / f"{game.name}_new" / name))

    def _tx_defaults(self, path):
        p = Path(path)
        game = p.parent
        if not self.tx_dir_var.get():
            self.tx_dir_var.set(str(game.parent / f"{game.name}_text"))
        if not self.tx_ref_var.get() and p.stem.lower() == 'script' and (game / "Update3.pac").exists():
            self.tx_ref_var.set(str(game / "Update3.pac"))
        if not self.tx_out_var.get():
            self.tx_out_var.set(str(game.parent / f"{game.name}_new" / self._free_update_name(game)))

    # ------------------------------------------------------ Pestaña Extraer

    def browse_src_dir(self):
        path = filedialog.askdirectory(title="Game folder")
        if path:
            self._set_src(path)

    def browse_src_pac(self):
        path = filedialog.askopenfilename(title="Open .pac file", filetypes=PAC_TYPES)
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
        try:
            pacs = nexas_extract.find_pacs(src)
        except OSError as e:
            self.src_info.configure(text=str(e))
            return
        total = sum(p.stat().st_size for p in pacs)
        names = ", ".join(p.stem for p in pacs) or "no .pac files"
        self.src_info.configure(text=f"{len(pacs)} .pac ({format_size(total)}): {names}")

    def start_extract(self):
        src = self.src_var.get().strip().strip('"')
        out_dir = self.out_var.get().strip()
        cats = [k for k, v in self.cat_vars.items() if v.get()]
        if not src or not Path(src).exists():
            messagebox.showwarning(APP_TITLE, "Choose the game folder or a .pac file.")
            return
        if not out_dir:
            messagebox.showwarning(APP_TITLE, "Choose an output folder.")
            return
        if not cats:
            messagebox.showwarning(APP_TITLE, "Choose at least one category.")
            return
        if not nexas_extract.find_pacs(src):
            messagebox.showwarning(APP_TITLE, "There are no .pac files to process.")
            return

        def done(r):
            text = f"{r.files} files ({r.converted} converted) and {r.extras} extra files in:\n{out_dir}"
            if r.skipped:
                text += f"\n({r.skipped} were already done)"
            if r.warnings:
                text += f"\n\n{len(r.warnings)} warnings:\n" + "\n".join(r.warnings[:5])
            if r.errors:
                text += f"\n\n{len(r.errors)} errors:\n" + "\n".join(r.errors[:10])
            if messagebox.askyesno(APP_TITLE, text + "\n\nOpen the folder?"):
                open_folder(out_dir)

        clear_image(self.preview)
        self.run_task(nexas_extract.extract_all, (src, out_dir),
                      {"categories": cats, "convert": self.convert_var.get(), "extras": self.extras_var.get(),
                       "sprites": self.sprites_var.get(), "resume": self.resume_var.get()},
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
        pac, folder, out = (v.get().strip() for v in (self.rp_pac_var, self.rp_dir_var, self.rp_out_var))
        if not pac or not Path(pac).is_file():
            messagebox.showwarning(APP_TITLE, "Choose the original .pac.")
            return
        if not folder or not Path(folder).is_dir():
            messagebox.showwarning(APP_TITLE, "Choose the folder with the extracted files.")
            return
        if not out or not self._confirm_overwrite(out, pac):
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

        self.run_task(nexas_extract.repack, (pac, folder, out),
                      {"patch_only": self.rp_patch_var.get(), "add_new": self.rp_add_var.get(),
                       "allow_resize": self.resize_var.get()}, "Repacking", done)

    # ------------------------------------------------------ Pestaña Texto

    def _load_charmap(self):
        path = self.charmap_var.get().strip()
        return nexas_script.CharMap.load(path) if path else None

    def _show_report(self, text):
        self.report_box.configure(state="normal")
        self.report_box.delete("1.0", "end")
        self.report_box.insert("1.0", text)
        self.report_box.configure(state="disabled")

    def start_text(self):
        pac, ref, folder = (v.get().strip() for v in (self.tx_pac_var, self.tx_ref_var, self.tx_dir_var))
        if not pac or not Path(pac).is_file():
            messagebox.showwarning(APP_TITLE, "Choose the script .pac.")
            return
        if ref and not Path(ref).is_file():
            messagebox.showwarning(APP_TITLE, "The reference .pac does not exist.")
            return
        if not folder:
            messagebox.showwarning(APP_TITLE, "Choose the text folder.")
            return
        try:
            charmap = self._load_charmap()
        except Exception as e:
            messagebox.showerror(APP_TITLE, str(e))
            return

        def done(r):
            lines = [f"{r.lines} lines from {r.files} files in {folder}",
                     "One file per script: INDEX|TEXT (tables: ROW:COLUMN|TEXT). Translate only the text."]
            lines += [f"[WARNING] {w}" for w in r.warnings]
            self._show_report("\n".join(lines))
            if messagebox.askyesno(APP_TITLE, f"{r.lines} lines from {r.files} files.\n\nOpen the folder?"):
                open_folder(folder)

        self.run_task(nexas_script.extract_text, (pac, folder),
                      {"reference": ref or None, "charmap": charmap, "with_listing": self.listing_var.get(),
                       "with_json": self.json_var.get()}, "Extracting text", done)

    def start_text_rebuild(self):
        pac, folder, out = (v.get().strip() for v in (self.tx_pac_var, self.tx_dir_var, self.tx_out_var))
        if not pac or not Path(pac).is_file():
            messagebox.showwarning(APP_TITLE, "Choose the script .pac.")
            return
        if not folder or not Path(folder).is_dir():
            messagebox.showwarning(APP_TITLE, "Choose the text folder.")
            return
        if not out or not self._confirm_overwrite(out, pac):
            return
        try:
            charmap = self._load_charmap()
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

        self.run_task(nexas_script.rebuild, (pac, folder, out),
                      {"charmap": charmap, "patch_only": self.tx_patch_var.get()}, "Rebuilding", done)

    # -------------------------------------------------- Pestaña Archivos PAC

    def browse_pac(self):
        path = filedialog.askopenfilename(title="Open .pac file", filetypes=PAC_TYPES)
        if path:
            self.load_pac(path)

    def load_pac(self, path):
        path = path.strip().strip('"')
        if not path:
            return
        try:
            with nexas_pac.Pac(path) as pac:
                entries, pack_type = pac.entries, pac.pack
        except Exception as e:
            messagebox.showerror(APP_TITLE, f"Could not read the file:\n{e}")
            return
        self.pac_var.set(path)
        self.entries = entries
        p = Path(path)
        self.raw_out_var.set(str(p.parent.parent / f"{p.parent.name}_{p.stem}"))
        types = Counter(e.ext or '(none)' for e in entries)
        total = sum(e.size for e in entries)
        self.pac_info.configure(text=f"{len(entries)} files   ·   {format_size(total)}   ·   "
                                     f"compression: {nexas_pac.PACK_NAMES[pack_type]}   ·   "
                                     + ", ".join(f"{n} {ext}" for ext, n in types.most_common()))
        self.type_menu.configure(values=[ALL_TYPES] + sorted(types))
        self.type_var.set(ALL_TYPES)
        self._refresh_tree()

    def _refresh_tree(self):
        self.tree.delete(*self.tree.get_children())
        text = self.filter_var.get().lower()
        ext = self.type_var.get()
        for i, e in enumerate(self.entries):
            if text in e.name.lower() and ext in (ALL_TYPES, e.ext or '(none)'):
                self.tree.insert("", "end", iid=str(i), text=e.name,
                                 values=(e.ext, format_size(e.size)))
        self._update_selection_label()

    def _update_selection_label(self):
        if not self.entries:
            self.sel_label.configure(text="")
            return
        self.sel_label.configure(text=f"{len(self.tree.get_children())} shown · "
                                      f"{len(self.tree.selection())} selected")

    def start_raw(self, only_selected):
        pac_path = self.pac_var.get().strip()
        out_dir = self.raw_out_var.get().strip()
        if not self.entries:
            messagebox.showwarning(APP_TITLE, "Open a .pac file first.")
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

        def done(count):
            if messagebox.askyesno(APP_TITLE, f"Extracted {count} files to:\n{out_dir}\n\nOpen the folder?"):
                open_folder(out_dir)

        self.run_task(nexas_pac.extract, (pac_path, out_dir), {"names": names}, "Extracting", done)

    def start_pack(self):
        folder = filedialog.askdirectory(title="Folder with the files to pack")
        if not folder:
            return
        out = filedialog.asksaveasfilename(title="Save .pac", defaultextension=".pac",
                                           initialfile=f"{Path(folder).name}.pac", filetypes=PAC_TYPES)
        if not out:
            return
        like = self.pac_var.get().strip() if self.entries else None
        if like and not messagebox.askyesno(APP_TITLE, f"Keep the order and compression of\n{Path(like).name}?"):
            like = None

        def done(count):
            if messagebox.askyesno(APP_TITLE, f"Archive created ({count} files):\n{out}\n\nOpen the folder?"):
                open_folder(Path(out).parent)

        self.run_task(nexas_pac.pack, (folder, out), {"order_from": like}, "Packing", done)

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
    main()
