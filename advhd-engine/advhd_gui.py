#!/usr/bin/env python3
"""
AdvHD Tools GUI - Interfaz gráfica para archivos .arc e imágenes WIPF (motor AdvHD de WillPlus)
Juego: CROSS†CHANNEL (edición WillPlus, 2012)
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

import advhd_extract
import advhd_wsc
from advhd_arc import pack, read_index
from advhd_common import Cancelled

APP_TITLE = "AdvHD Tools"
PREVIEW_SIZE = (360, 270)
PREVIEW_INTERVAL = 0.5      # segundos entre actualizaciones de la vista previa
ALL_TYPES = "Todos"


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


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("880x640")
        self.minsize(700, 520)

        self.events = queue.Queue()
        self.cancel_event = threading.Event()
        self.worker = None
        self.busy_widgets = []

        # Estado de la pestaña Archivos ARC
        self.entries = []           # lista de advhd_arc.ArcEntry
        # Estado de la pestaña Imágenes
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
        self._build_images_tab(self.tabs.add("Imágenes"))
        self._build_repack_tab(self.tabs.add("Reempaquetar"))
        self._build_scripts_tab(self.tabs.add("Guiones"))
        self._build_audio_tab(self.tabs.add("Audio"))
        self._build_arc_tab(self.tabs.add("Archivos ARC"))
        self._build_status_bar()

    def _path_row(self, tab, row, label, var, command, button="Elegir…"):
        """Fila etiqueta + campo + botón."""
        ctk.CTkLabel(tab, text=label).grid(row=row, column=0, sticky="w", padx=(4, 8), pady=6)
        ctk.CTkEntry(tab, textvariable=var).grid(row=row, column=1, sticky="ew", pady=6)
        btn = ctk.CTkButton(tab, text=button, width=120, command=command)
        btn.grid(row=row, column=2, padx=(8, 4), pady=6)
        self.busy_widgets.append(btn)

    def _note(self, tab, row, text):
        ctk.CTkLabel(tab, text=text, justify="left", anchor="w", text_color=("gray35", "gray65")) \
            .grid(row=row, column=1, columnspan=2, sticky="ew", pady=(0, 6))

    def _build_repack_tab(self, tab):
        tab.grid_columnconfigure(1, weight=1)
        self.rp_arc_var = tk.StringVar()
        self.rp_dir_var = tk.StringVar()
        self.rp_out_var = tk.StringVar()
        self._path_row(tab, 0, ".arc original:", self.rp_arc_var,
                       lambda: self._ask_open(self.rp_arc_var, "Archivo .arc original", self._rp_defaults))
        self._path_row(tab, 1, "Carpeta de imágenes:", self.rp_dir_var,
                       lambda: self._ask_dir(self.rp_dir_var, "Carpeta con las imágenes extraídas"))
        self._note(tab, 2, "La carpeta de la extracción (o la de ese .arc dentro de ella). Solo se "
                           "vuelven a codificar\nlas imágenes que editaste; el resto se copia del original.")
        self._path_row(tab, 3, ".arc de salida:", self.rp_out_var,
                       lambda: self._ask_save(self.rp_out_var, "Guardar .arc"), "Guardar como…")
        self.resize_var = tk.BooleanVar(value=False)
        cb = ctk.CTkCheckBox(tab, text="Permitir imágenes con otro tamaño que el original",
                             variable=self.resize_var)
        cb.grid(row=4, column=1, sticky="w", pady=6)
        btn = ctk.CTkButton(tab, text="Crear .arc", width=160, command=self.start_repack)
        btn.grid(row=5, column=2, sticky="e", padx=4, pady=(18, 0))
        self.busy_widgets += [cb, btn]

    def _build_scripts_tab(self, tab):
        tab.grid_columnconfigure(1, weight=1)
        tab.grid_rowconfigure(8, weight=1)
        self.rio_var = tk.StringVar()
        self.txt_dir_var = tk.StringVar()
        self.charmap_var = tk.StringVar()
        self.rio_out_var = tk.StringVar()
        self._path_row(tab, 0, "Rio.arc:", self.rio_var,
                       lambda: self._ask_open(self.rio_var, "Archivo de guiones (Rio.arc)", self._rio_defaults))
        self._path_row(tab, 1, "Carpeta de textos:", self.txt_dir_var,
                       lambda: self._ask_dir(self.txt_dir_var, "Carpeta de textos"))
        self._path_row(tab, 2, "Tabla de caracteres:", self.charmap_var,
                       lambda: self._ask_open(self.charmap_var, "Tabla de caracteres (opcional)",
                                              types=[("Texto", "*.txt"), ("Todos", "*.*")]))
        self._note(tab, 3, "Opcional: para letras que no existen en Shift-JIS (ñ, á…) con una fuente propia.")

        row = ctk.CTkFrame(tab, fg_color="transparent")
        row.grid(row=4, column=1, columnspan=2, sticky="ew", pady=6)
        self.listing_var = tk.BooleanVar(value=False)
        cb = ctk.CTkCheckBox(row, text="Guardar también el listado completo de cada guion",
                             variable=self.listing_var)
        cb.pack(side="left")
        btn = ctk.CTkButton(row, text="Extraer textos", width=140, command=self.start_wsc_strings)
        btn.pack(side="right", padx=(8, 4))
        self.busy_widgets += [cb, btn]

        self._path_row(tab, 5, "Rio.arc de salida:", self.rio_out_var,
                       lambda: self._ask_save(self.rio_out_var, "Guardar Rio.arc"), "Guardar como…")
        row = ctk.CTkFrame(tab, fg_color="transparent")
        row.grid(row=6, column=1, columnspan=2, sticky="ew", pady=6)
        self.fixed_var = tk.BooleanVar(value=False)
        cb = ctk.CTkCheckBox(row, text="Tamaño fijo (cada texto conserva su tamaño original)",
                             variable=self.fixed_var)
        cb.pack(side="left")
        btn = ctk.CTkButton(row, text="Reconstruir", width=140, command=self.start_wsc_rebuild)
        btn.pack(side="right", padx=(8, 4))
        self.busy_widgets += [cb, btn]

        self.wsc_report = ctk.CTkTextbox(tab, height=120, wrap="none")
        self.wsc_report.grid(row=8, column=0, columnspan=3, sticky="nsew", padx=4, pady=(8, 0))
        self.wsc_report.configure(state="disabled")

    def _build_audio_tab(self, tab):
        tab.grid_columnconfigure(1, weight=1)
        self.au_src_var = tk.StringVar()
        self.au_out_var = tk.StringVar()
        self._path_row(tab, 0, "Carpeta del juego:", self.au_src_var, self._au_browse)
        self._path_row(tab, 1, "Destino:", self.au_out_var,
                       lambda: self._ask_dir(self.au_out_var, "Carpeta de destino"))
        self._note(tab, 2, "Copia los OGG de Bgm.arc, Se.arc y Voice.arc (casi 600 MB) en una carpeta por "
                           "archivo.\nPara volver a crear un .arc de audio usa «Empaquetar carpeta…» en "
                           "Archivos ARC.")
        btn = ctk.CTkButton(tab, text="Extraer audio", width=160, command=self.start_audio)
        btn.grid(row=3, column=2, sticky="e", padx=4, pady=(18, 0))
        self.busy_widgets.append(btn)

    # ------------------------------------------------- Diálogos comunes

    def _ask_open(self, var, title, then=None, types=None):
        path = filedialog.askopenfilename(title=title, filetypes=types or [("Archivos ARC", "*.arc"),
                                                                           ("Todos", "*.*")])
        if path:
            var.set(path)
            if then:
                then(path)

    def _ask_dir(self, var, title):
        path = filedialog.askdirectory(title=title)
        if path:
            var.set(path)

    def _ask_save(self, var, title):
        initial = Path(var.get() or "nuevo.arc")
        path = filedialog.asksaveasfilename(title=title, defaultextension=".arc",
                                            initialdir=str(initial.parent) if initial.parent.exists() else None,
                                            initialfile=initial.name,
                                            filetypes=[("Archivos ARC", "*.arc"), ("Todos", "*.*")])
        if path:
            var.set(path)

    def _confirm_overwrite(self, out_path, source) -> bool:
        out, src = Path(out_path).resolve(), Path(source).resolve()
        if out == src:
            messagebox.showwarning(APP_TITLE, "La salida no puede ser el archivo original.")
            return False
        return not out.exists() or messagebox.askyesno(APP_TITLE, f"{out.name} ya existe.\n¿Sobrescribirlo?")

    def _rp_defaults(self, path):
        p = Path(path)
        if not self.rp_out_var.get():
            self.rp_out_var.set(str(p.parent.parent / f"{p.parent.name}_nuevo" / p.name))
        if not self.rp_dir_var.get() and self.img_out_var.get():
            self.rp_dir_var.set(self.img_out_var.get())

    def _rio_defaults(self, path):
        p = Path(path)
        game = p.parent
        if not self.txt_dir_var.get():
            self.txt_dir_var.set(str(game.parent / f"{game.name}_textos"))
        if not self.rio_out_var.get():
            self.rio_out_var.set(str(game.parent / f"{game.name}_nuevo" / p.name))

    def _au_browse(self):
        path = filedialog.askdirectory(title="Carpeta del juego")
        if path:
            self.au_src_var.set(path)
            if not self.au_out_var.get():
                self.au_out_var.set(str(Path(path).parent / f"{Path(path).name}_audio"))

    def _build_images_tab(self, tab):
        tab.grid_columnconfigure(1, weight=1)
        tab.grid_rowconfigure(5, weight=1)

        self.src_var = tk.StringVar()
        ctk.CTkLabel(tab, text="Carpeta del juego o .arc:").grid(row=0, column=0, sticky="w",
                                                                 padx=(4, 8), pady=6)
        src_entry = ctk.CTkEntry(tab, textvariable=self.src_var)
        src_entry.grid(row=0, column=1, sticky="ew", pady=6)
        src_entry.bind("<Return>", lambda _e: self._update_images_info())
        src_entry.bind("<FocusOut>", lambda _e: self._update_images_info())
        btns = ctk.CTkFrame(tab, fg_color="transparent")
        btns.grid(row=0, column=2, padx=(8, 4), pady=6)
        b1 = ctk.CTkButton(btns, text="Carpeta…", width=80, command=self.browse_src_dir)
        b1.pack(side="left")
        b2 = ctk.CTkButton(btns, text=".arc…", width=60, command=self.browse_src_arc)
        b2.pack(side="left", padx=(6, 0))
        self.busy_widgets += [b1, b2]

        self.src_info = ctk.CTkLabel(tab, text="", anchor="w", text_color=("gray35", "gray65"))
        self.src_info.grid(row=1, column=1, columnspan=2, sticky="ew")

        self.img_out_var = tk.StringVar()
        ctk.CTkLabel(tab, text="Destino:").grid(row=2, column=0, sticky="w", padx=(4, 8), pady=6)
        ctk.CTkEntry(tab, textvariable=self.img_out_var).grid(row=2, column=1, sticky="ew", pady=6)
        btn = ctk.CTkButton(tab, text="Elegir…", width=146, command=self.browse_img_out)
        btn.grid(row=2, column=2, padx=(8, 4), pady=6)
        self.busy_widgets.append(btn)

        opts = ctk.CTkFrame(tab, fg_color="transparent")
        opts.grid(row=3, column=1, columnspan=2, sticky="ew", pady=6)
        self.resume_var = tk.BooleanVar(value=True)
        self.audio_var = tk.BooleanVar(value=False)
        self.no_alpha_var = tk.BooleanVar(value=False)
        cb1 = ctk.CTkCheckBox(opts, text="Reanudar", variable=self.resume_var)
        cb1.pack(side="left")
        cb2 = ctk.CTkCheckBox(opts, text="Incluir audio", variable=self.audio_var,
                              command=self._update_images_info)
        cb2.pack(side="left", padx=(16, 0))
        cb3 = ctk.CTkCheckBox(opts, text="Sin componer alfa", variable=self.no_alpha_var)
        cb3.pack(side="left", padx=(16, 0))
        ctk.CTkLabel(opts, text="Procesos:").pack(side="left", padx=(16, 6))
        cores = os.cpu_count() or 1
        self.workers_var = tk.StringVar(value=str(advhd_extract.default_workers()))
        om = ctk.CTkOptionMenu(opts, values=[str(n) for n in range(1, cores + 1)], width=70,
                               variable=self.workers_var)
        om.pack(side="left")
        self.busy_widgets += [cb1, cb2, cb3, om]

        btn = ctk.CTkButton(tab, text="Extraer imágenes", width=160, command=self.start_images)
        btn.grid(row=4, column=2, sticky="e", padx=4, pady=(6, 0))
        self.busy_widgets.append(btn)
        ctk.CTkLabel(tab, text="Las imágenes con máscara (MSK) se guardan como PNG con transparencia.",
                     anchor="w", text_color=("gray35", "gray65")) \
            .grid(row=4, column=1, sticky="w", pady=(6, 0))

        self.img_preview = ctk.CTkLabel(tab, text="")
        self.img_preview.grid(row=5, column=0, columnspan=3, sticky="nsew", pady=8)

    def _build_arc_tab(self, tab):
        tab.grid_columnconfigure(1, weight=1)
        tab.grid_rowconfigure(3, weight=1)

        self.arc_var = tk.StringVar()
        ctk.CTkLabel(tab, text="Archivo .arc:").grid(row=0, column=0, sticky="w", padx=(4, 8), pady=4)
        arc_entry = ctk.CTkEntry(tab, textvariable=self.arc_var)
        arc_entry.grid(row=0, column=1, sticky="ew", pady=4)
        arc_entry.bind("<Return>", lambda _e: self.load_arc(self.arc_var.get()))
        btn = ctk.CTkButton(tab, text="Abrir…", width=100, command=self.browse_arc)
        btn.grid(row=0, column=2, padx=(8, 4), pady=4)
        self.busy_widgets.append(btn)

        self.arc_info = ctk.CTkLabel(tab, text="Ningún archivo abierto", anchor="w",
                                     text_color=("gray35", "gray65"))
        self.arc_info.grid(row=1, column=0, columnspan=3, sticky="ew", padx=4)

        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *_: self._refresh_tree())
        ctk.CTkLabel(tab, text="Filtrar:").grid(row=2, column=0, sticky="w", padx=(4, 8), pady=4)
        filt = ctk.CTkFrame(tab, fg_color="transparent")
        filt.grid(row=2, column=1, columnspan=2, sticky="ew", padx=(0, 4), pady=4)
        filt.grid_columnconfigure(0, weight=1)
        ctk.CTkEntry(filt, textvariable=self.filter_var, placeholder_text="nombre contiene…") \
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
                                 style="AdvHD.Treeview")
        self.tree.heading("#0", text="Nombre", anchor="w")
        self.tree.heading("type", text="Tipo", anchor="w")
        self.tree.heading("size", text="Tamaño", anchor="e")
        self.tree.column("#0", stretch=True, width=400)
        self.tree.column("type", stretch=False, width=80, anchor="w")
        self.tree.column("size", stretch=False, width=110, anchor="e")
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ctk.CTkScrollbar(tree_frame, command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._update_selection_label())

        self.raw_out_var = tk.StringVar()
        ctk.CTkLabel(tab, text="Destino:").grid(row=4, column=0, sticky="w", padx=(4, 8), pady=4)
        ctk.CTkEntry(tab, textvariable=self.raw_out_var).grid(row=4, column=1, sticky="ew", pady=4)
        btn = ctk.CTkButton(tab, text="Elegir…", width=100, command=self.browse_raw_out)
        btn.grid(row=4, column=2, padx=(8, 4), pady=4)
        self.busy_widgets.append(btn)

        actions = ctk.CTkFrame(tab, fg_color="transparent")
        actions.grid(row=5, column=0, columnspan=3, sticky="ew", padx=4, pady=(4, 0))
        self.sel_label = ctk.CTkLabel(actions, text="", text_color=("gray35", "gray65"))
        self.sel_label.pack(side="left")
        btn_all = ctk.CTkButton(actions, text="Extraer todo", command=lambda: self.start_raw(False))
        btn_all.pack(side="right")
        btn_sel = ctk.CTkButton(actions, text="Extraer seleccionados",
                                command=lambda: self.start_raw(True))
        btn_sel.pack(side="right", padx=8)
        btn_pack = ctk.CTkButton(actions, text="Empaquetar carpeta…", fg_color="gray40",
                                 hover_color="gray30", command=self.start_pack)
        btn_pack.pack(side="right", padx=(0, 8))
        self.busy_widgets += [btn_all, btn_sel, btn_pack]

        ctk.CTkLabel(tab, text="Los archivos se guardan tal cual (los WIP y MSK siguen en formato WIPF).",
                     anchor="w", text_color=("gray35", "gray65")) \
            .grid(row=6, column=0, columnspan=3, sticky="ew", padx=4, pady=(4, 0))

    def _build_status_bar(self):
        bar = ctk.CTkFrame(self)
        bar.grid(row=1, column=0, sticky="ew", padx=12, pady=12)
        bar.grid_columnconfigure(1, weight=1)

        self.progress = ctk.CTkProgressBar(bar)
        self.progress.set(0)
        self.progress.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(10, 4))

        self.status = ctk.CTkLabel(bar, text="Listo", anchor="w")
        self.status.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 8))

        self.cancel_btn = ctk.CTkButton(bar, text="Cancelar", width=90, state="disabled",
                                        fg_color="#b23b3b", hover_color="#8f2f2f",
                                        command=self.cancel_task)
        self.cancel_btn.grid(row=0, column=2, rowspan=2, padx=(0, 10))

        theme = ctk.CTkOptionMenu(bar, values=["Sistema", "Claro", "Oscuro"], width=100,
                                  command=self._change_theme)
        theme.grid(row=0, column=3, rowspan=2, padx=(0, 10))

    # --------------------------------------------------------------- Tema

    def _change_theme(self, value):
        ctk.set_appearance_mode({"Sistema": "system", "Claro": "light", "Oscuro": "dark"}[value])
        self.after(10, self._apply_tree_style)

    def _apply_tree_style(self):
        dark = ctk.get_appearance_mode() == "Dark"
        bg, fg = ("#2b2b2b", "#dce4ee") if dark else ("#ffffff", "#1a1a1a")
        head_bg = "#3a3a3a" if dark else "#e5e5e5"
        sel = "#1f6aa5"
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("AdvHD.Treeview", background=bg, fieldbackground=bg, foreground=fg,
                        rowheight=24, borderwidth=0)
        style.map("AdvHD.Treeview", background=[("selected", sel)], foreground=[("selected", "#ffffff")])
        style.configure("AdvHD.Treeview.Heading", background=head_bg, foreground=fg, relief="flat")
        style.map("AdvHD.Treeview.Heading", background=[("active", head_bg)])

    # ------------------------------------------------------ Pestaña Imágenes

    def browse_src_dir(self):
        path = filedialog.askdirectory(title="Carpeta del juego")
        if path:
            self._set_src(path)

    def browse_src_arc(self):
        path = filedialog.askopenfilename(title="Abrir archivo .arc",
                                          filetypes=[("Archivos ARC", "*.arc"), ("Todos", "*.*")])
        if path:
            self._set_src(path)

    def _set_src(self, path):
        self.src_var.set(path)
        if not self.img_out_var.get():
            # Junto a la carpeta del juego, nunca dentro
            p = Path(path)
            game_dir = p.parent if p.is_file() else p
            self.img_out_var.set(str(game_dir.parent / f"{game_dir.name}_png"))
        self._update_images_info()

    def _update_images_info(self):
        src = self.src_var.get().strip().strip('"')
        if not src or not Path(src).exists():
            self.src_info.configure(text="")
            return
        try:
            arcs = advhd_extract.find_arcs(src, self.audio_var.get())
        except OSError as e:
            self.src_info.configure(text=str(e))
            return
        names = ", ".join(p.name for p in arcs) or "ningún .arc"
        self.src_info.configure(text=f"Se procesará: {names}")

    def browse_img_out(self):
        path = filedialog.askdirectory(title="Carpeta de destino")
        if path:
            self.img_out_var.set(path)

    def start_images(self):
        src = self.src_var.get().strip().strip('"')
        out_dir = self.img_out_var.get().strip()
        if not src or not Path(src).exists():
            messagebox.showwarning(APP_TITLE, "Elige la carpeta del juego o un archivo .arc.")
            return
        if not out_dir:
            messagebox.showwarning(APP_TITLE, "Elige una carpeta de destino.")
            return
        if not advhd_extract.find_arcs(src, self.audio_var.get()):
            messagebox.showwarning(APP_TITLE, "No hay archivos .arc que procesar.")
            return

        def done(r):
            text = f"{r.images} imágenes y {r.others} otros archivos en:\n{out_dir}"
            if r.skipped:
                text += f"\n({r.skipped} nombres ya estaban hechos)"
            if r.warnings:
                text += f"\n\n{len(r.warnings)} avisos:\n" + "\n".join(r.warnings[:5])
            if r.errors:
                text += f"\n\n{len(r.errors)} errores:\n" + "\n".join(r.errors[:10])
            if messagebox.askyesno(APP_TITLE, text + "\n\n¿Abrir la carpeta?"):
                open_folder(out_dir)

        self.img_preview.configure(image=None, text="")
        self.run_task(advhd_extract.extract_images, (src, out_dir),
                      {"resume": self.resume_var.get(), "include_audio": self.audio_var.get(),
                       "compose": not self.no_alpha_var.get(),
                       "workers": int(self.workers_var.get())},
                      "Extrayendo imágenes", done)

    def _maybe_preview(self, name):
        """Muestra el último PNG escrito, como mucho cada PREVIEW_INTERVAL segundos."""
        if not name.lower().endswith(".png") or time.time() - self._last_preview < PREVIEW_INTERVAL:
            return
        self._last_preview = time.time()
        try:
            with Image.open(name) as img:
                img.load()
                self.img_preview.configure(image=preview_image(img), text="")
        except OSError:
            pass

    # -------------------------------------------------- Pestaña Archivos ARC

    def browse_arc(self):
        path = filedialog.askopenfilename(title="Abrir archivo .arc",
                                          filetypes=[("Archivos ARC", "*.arc"), ("Todos", "*.*")])
        if path:
            self.load_arc(path)

    def load_arc(self, path):
        path = path.strip().strip('"')
        if not path:
            return
        try:
            entries = read_index(path)
        except Exception as e:
            messagebox.showerror(APP_TITLE, f"No se pudo leer el archivo:\n{e}")
            return
        self.arc_var.set(path)
        self.entries = entries
        p = Path(path)
        self.raw_out_var.set(str(p.parent.parent / f"{p.parent.name}_{p.stem}"))

        types = Counter(e.ext for e in entries)
        total = sum(e.size for e in entries)
        self.arc_info.configure(text=f"{len(entries)} archivos   ·   {format_size(total)}   ·   "
                                     + ", ".join(f"{n} {ext}" for ext, n in types.items()))
        self.type_menu.configure(values=[ALL_TYPES] + sorted(types))
        self.type_var.set(ALL_TYPES)
        self._refresh_tree()

    def _refresh_tree(self):
        self.tree.delete(*self.tree.get_children())
        text = self.filter_var.get().lower()
        ext = self.type_var.get()
        for i, e in enumerate(self.entries):
            if text in e.name.lower() and ext in (ALL_TYPES, e.ext):
                self.tree.insert("", "end", iid=str(i), text=e.name,
                                 values=(e.ext, format_size(e.size)))
        self._update_selection_label()

    def _update_selection_label(self):
        if not self.entries:
            self.sel_label.configure(text="")
            return
        shown = len(self.tree.get_children())
        selected = len(self.tree.selection())
        self.sel_label.configure(text=f"{shown} mostrados · {selected} seleccionados")

    def browse_raw_out(self):
        path = filedialog.askdirectory(title="Carpeta de destino")
        if path:
            self.raw_out_var.set(path)

    def start_raw(self, only_selected):
        arc_path = self.arc_var.get().strip()
        out_dir = self.raw_out_var.get().strip()
        if not self.entries:
            messagebox.showwarning(APP_TITLE, "Primero abre un archivo .arc.")
            return
        if not out_dir:
            messagebox.showwarning(APP_TITLE, "Elige una carpeta de destino.")
            return
        names = None
        if only_selected:
            names = [self.entries[int(iid)].filename for iid in self.tree.selection()]
            if not names:
                messagebox.showwarning(APP_TITLE, "No hay archivos seleccionados.")
                return

        def done(count):
            if messagebox.askyesno(APP_TITLE, f"Se extrajeron {count} archivos en:\n{out_dir}\n\n"
                                              "¿Abrir la carpeta?"):
                open_folder(out_dir)

        self.run_task(advhd_extract.extract_raw, (arc_path, out_dir), {"names": names},
                      "Extrayendo", done)

    def start_pack(self):
        folder = filedialog.askdirectory(title="Carpeta con los archivos NOMBRE.ext a empaquetar")
        if not folder:
            return
        out = filedialog.asksaveasfilename(title="Guardar .arc", defaultextension=".arc",
                                           initialfile=f"{Path(folder).name}.arc",
                                           filetypes=[("Archivos ARC", "*.arc"), ("Todos", "*.*")])
        if not out:
            return

        def done(count):
            if messagebox.askyesno(APP_TITLE, f"Paquete creado ({count} archivos):\n{out}\n\n"
                                              "¿Abrir la carpeta?"):
                open_folder(Path(out).parent)

        self.run_task(pack, (folder, out), {}, "Empaquetando", done)

    # ------------------------------------------------- Pestaña Reempaquetar

    def start_repack(self):
        arc, folder, out = (v.get().strip() for v in (self.rp_arc_var, self.rp_dir_var, self.rp_out_var))
        if not arc or not Path(arc).is_file():
            messagebox.showwarning(APP_TITLE, "Elige el .arc original.")
            return
        if not folder or not Path(folder).is_dir():
            messagebox.showwarning(APP_TITLE, "Elige la carpeta con las imágenes extraídas.")
            return
        if not out or not self._confirm_overwrite(out, arc):
            return

        def done(r):
            if r.errors:
                messagebox.showerror(APP_TITLE, "No se creó el archivo. Errores:\n\n" +
                                     "\n".join(r.errors[:15]))
                return
            changed = "\n".join(r.changed[:15]) + ("\n…" if len(r.changed) > 15 else "")
            if messagebox.askyesno(APP_TITLE, f"{len(r.changed)} de {r.entries} archivos reemplazados"
                                              f"{':' if r.changed else '.'}\n{changed}\n\n{out}\n\n"
                                              "¿Abrir la carpeta?"):
                open_folder(Path(out).parent)

        self.run_task(advhd_extract.pack_images, (arc, folder, out),
                      {"allow_resize": self.resize_var.get()}, "Reempaquetando", done)

    # ---------------------------------------------------- Pestaña Guiones

    def _load_charmap(self):
        path = self.charmap_var.get().strip()
        return advhd_wsc.CharMap.load(path) if path else None

    def _show_report(self, text):
        self.wsc_report.configure(state="normal")
        self.wsc_report.delete("1.0", "end")
        self.wsc_report.insert("1.0", text)
        self.wsc_report.configure(state="disabled")

    def start_wsc_strings(self):
        rio, folder = self.rio_var.get().strip(), self.txt_dir_var.get().strip()
        if not rio or not Path(rio).is_file():
            messagebox.showwarning(APP_TITLE, "Elige Rio.arc.")
            return
        if not folder:
            messagebox.showwarning(APP_TITLE, "Elige la carpeta de textos.")
            return
        try:
            charmap = self._load_charmap()
        except Exception as e:
            messagebox.showerror(APP_TITLE, str(e))
            return

        def done(result):
            files, strings = result
            self._show_report(f"{strings} textos de {files} guiones en {folder}\n"
                              "Un archivo por guion: ID|DIRECCIÓN|TEXTO. Traduce solo el texto.")
            if messagebox.askyesno(APP_TITLE, f"{strings} textos de {files} guiones.\n\n¿Abrir la carpeta?"):
                open_folder(folder)

        self.run_task(advhd_wsc.extract_strings, (rio, folder),
                      {"charmap": charmap, "with_listing": self.listing_var.get()},
                      "Extrayendo textos", done)

    def start_wsc_rebuild(self):
        rio, folder, out = (v.get().strip() for v in (self.rio_var, self.txt_dir_var, self.rio_out_var))
        if not rio or not Path(rio).is_file():
            messagebox.showwarning(APP_TITLE, "Elige Rio.arc.")
            return
        if not folder or not Path(folder).is_dir():
            messagebox.showwarning(APP_TITLE, "Elige la carpeta de textos.")
            return
        if not out or not self._confirm_overwrite(out, rio):
            return
        try:
            charmap = self._load_charmap()
        except Exception as e:
            messagebox.showerror(APP_TITLE, str(e))
            return

        def done(rep):
            lines = [rep.summary(), f"Salida: {out}", ""]
            lines += [f"[AVISO] {w}" for w in rep.warnings]
            for e in rep.entries:
                if advhd_wsc.ST_CUT in e.flags or advhd_wsc.ST_UNSUPPORTED in e.flags \
                        or advhd_wsc.ST_REPLACED in e.flags:
                    lines.append(f"[{', '.join(e.flags)}] {e.script} {e.sid:04d}: {e.detail}")
            self._show_report("\n".join(lines))

        self.run_task(advhd_wsc.rebuild, (rio, folder, out),
                      {"charmap": charmap, "mode": "fixed" if self.fixed_var.get() else "reloc"},
                      "Reconstruyendo guiones", done)

    # ------------------------------------------------------ Pestaña Audio

    def start_audio(self):
        src, out = self.au_src_var.get().strip(), self.au_out_var.get().strip()
        if not src or not Path(src).exists():
            messagebox.showwarning(APP_TITLE, "Elige la carpeta del juego.")
            return
        if not out:
            messagebox.showwarning(APP_TITLE, "Elige una carpeta de destino.")
            return

        def done(count):
            if messagebox.askyesno(APP_TITLE, f"{count} archivos de audio en:\n{out}\n\n¿Abrir la carpeta?"):
                open_folder(out)

        self.run_task(advhd_extract.extract_audio, (src, out), {}, "Extrayendo audio", done)

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
            while True:
                kind, data = self.events.get_nowait()
                if kind == "progress":
                    i, total, name = data
                    self.progress.set(i / total if total else 1)
                    if name:
                        self.status.configure(text=f"{self._task_label} [{i + 1}/{total}]  "
                                                   f"{Path(name).name}")
                        self._maybe_preview(name)
                elif kind == "done":
                    self._finish("Completado")
                    self.progress.set(1)
                    self._task_done(data)
                elif kind == "cancelled":
                    self._finish("Cancelado")
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
        self.status.configure(text="Cancelando… (se terminan las imágenes en curso)")

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
