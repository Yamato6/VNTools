#!/usr/bin/env python3
"""
Yeti Tools GUI - Archivos .cpk, imágenes XTX, guiones y audio (motor Yeti/Regista)
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
from yeti_common import Cancelled
from yeti_cpk import CPK
from yeti_xtx import xtx_info, xtx_to_image

APP_TITLE = "Yeti Tools"
PREVIEW_SIZE = (360, 270)
PREVIEW_INTERVAL = 0.5      # segundos entre actualizaciones de la vista previa


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


def preview_image(img: Image.Image) -> ctk.CTkImage:
    img = img.copy()
    img.thumbnail(PREVIEW_SIZE)
    return ctk.CTkImage(light_image=img, dark_image=img, size=img.size)


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

        # Estado de la pestaña Archivos CPK
        self.entries = []           # lista de yeti_cpk.Entry
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
        self._build_cpk_tab(self.tabs.add("Archivos CPK"))
        self._build_xtx_tab(self.tabs.add("XTX"))
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
        self.rp_cpk_var = tk.StringVar()
        self.rp_dir_var = tk.StringVar()
        self.rp_out_var = tk.StringVar()
        self._path_row(tab, 0, ".cpk original:", self.rp_cpk_var,
                       lambda: self._ask_open(self.rp_cpk_var, "Archivo .cpk original", self._rp_defaults))
        self._path_row(tab, 1, "Carpeta de imágenes:", self.rp_dir_var,
                       lambda: self._ask_dir(self.rp_dir_var, "Carpeta con las imágenes extraídas"))
        self._note(tab, 2, "La carpeta de la extracción (o la de ese .cpk dentro de ella). Solo se vuelven "
                           "a codificar las texturas\nque editaste, con su formato original; el resto se "
                           "copia tal cual.")
        self._path_row(tab, 3, ".cpk de salida:", self.rp_out_var,
                       lambda: self._ask_save(self.rp_out_var, "Guardar .cpk", ".cpk"), "Guardar como…")
        btn = ctk.CTkButton(tab, text="Crear .cpk", width=160, command=self.start_repack)
        btn.grid(row=4, column=2, sticky="e", padx=4, pady=(18, 0))
        self.busy_widgets.append(btn)

    def _build_scripts_tab(self, tab):
        tab.grid_columnconfigure(1, weight=1)
        tab.grid_rowconfigure(8, weight=1)
        self.sn_var = tk.StringVar()
        self.txt_dir_var = tk.StringVar()
        self.charmap_var = tk.StringVar()
        self.sn_out_var = tk.StringVar()
        self._path_row(tab, 0, "sn.bin:", self.sn_var,
                       lambda: self._ask_open(self.sn_var, "Archivo de guiones (sn.bin)", self._sn_defaults,
                                              [("sn.bin", "*.bin"), ("Todos", "*.*")]))
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
        btn = ctk.CTkButton(row, text="Extraer textos", width=140, command=self.start_script_strings)
        btn.pack(side="right", padx=(8, 4))
        self.busy_widgets += [cb, btn]

        self._path_row(tab, 5, "sn.bin de salida:", self.sn_out_var,
                       lambda: self._ask_save(self.sn_out_var, "Guardar sn.bin", ".bin"), "Guardar como…")
        row = ctk.CTkFrame(tab, fg_color="transparent")
        row.grid(row=6, column=1, columnspan=2, sticky="ew", pady=6)
        self.fixed_var = tk.BooleanVar(value=False)
        cb = ctk.CTkCheckBox(row, text="Tamaño fijo (cada texto conserva su tamaño original)",
                             variable=self.fixed_var)
        cb.pack(side="left")
        btn = ctk.CTkButton(row, text="Reconstruir", width=140, command=self.start_script_rebuild)
        btn.pack(side="right", padx=(8, 4))
        self.busy_widgets += [cb, btn]

        self.script_report = ctk.CTkTextbox(tab, height=120, wrap="none")
        self.script_report.grid(row=8, column=0, columnspan=3, sticky="nsew", padx=4, pady=(8, 0))
        self.script_report.configure(state="disabled")

    def _build_audio_tab(self, tab):
        tab.grid_columnconfigure(1, weight=1)
        self.au_src_var = tk.StringVar()
        self.au_out_var = tk.StringVar()
        self._path_row(tab, 0, "Carpeta data:", self.au_src_var, self._au_browse)
        self._path_row(tab, 1, "Destino:", self.au_out_var,
                       lambda: self._ask_dir(self.au_out_var, "Carpeta de destino"))
        self.wav_var = tk.BooleanVar(value=True)
        cb = ctk.CTkCheckBox(tab, text="Convertir también a WAV", variable=self.wav_var)
        cb.grid(row=2, column=1, sticky="w", pady=6)
        vgm = yeti_extract.find_vgmstream()
        self._note(tab, 3, ("vgmstream-cli encontrado: se puede convertir a WAV." if vgm else
                            "No se encontró vgmstream-cli: el audio se guarda solo como .hca. Para "
                            "convertir a WAV descarga\nvgmstream (vgmstream.org) y deja vgmstream-cli.exe "
                            "en el PATH o junto a esta herramienta.") +
                   "\nPara volver a meter audio en un .cpk usa «Reempaquetar carpeta…» en Archivos CPK "
                   "(archivos <id>.hca).")
        btn = ctk.CTkButton(tab, text="Extraer audio", width=160, command=self.start_audio)
        btn.grid(row=4, column=2, sticky="e", padx=4, pady=(18, 0))
        self.busy_widgets += [cb, btn]

    # ------------------------------------------------- Diálogos comunes

    def _ask_open(self, var, title, then=None, types=None):
        path = filedialog.askopenfilename(title=title, filetypes=types or [("Archivos CPK", "*.cpk"),
                                                                           ("Todos", "*.*")])
        if path:
            var.set(path)
            if then:
                then(path)

    def _ask_dir(self, var, title):
        path = filedialog.askdirectory(title=title)
        if path:
            var.set(path)

    def _ask_save(self, var, title, ext):
        initial = Path(var.get() or f"nuevo{ext}")
        path = filedialog.asksaveasfilename(title=title, defaultextension=ext,
                                            initialdir=str(initial.parent) if initial.parent.exists() else None,
                                            initialfile=initial.name,
                                            filetypes=[(ext, f"*{ext}"), ("Todos", "*.*")])
        if path:
            var.set(path)

    def _confirm_overwrite(self, out_path, source) -> bool:
        out, src = Path(out_path).resolve(), Path(source).resolve()
        if out == src:
            messagebox.showwarning(APP_TITLE, "La salida no puede ser el archivo original.")
            return False
        return not out.exists() or messagebox.askyesno(APP_TITLE, f"{out.name} ya existe.\n¿Sobrescribirlo?")

    def _new_data_dir(self, path) -> Path:
        """<juego>/data_nuevo junto a la carpeta data, para las salidas."""
        data = Path(path).parent
        return data.parent / f"{data.name}_nuevo"

    def _rp_defaults(self, path):
        if not self.rp_out_var.get():
            self.rp_out_var.set(str(self._new_data_dir(path) / Path(path).name))
        if not self.rp_dir_var.get() and self.img_out_var.get():
            self.rp_dir_var.set(self.img_out_var.get())

    def _sn_defaults(self, path):
        data = Path(path).parent
        if not self.txt_dir_var.get():
            self.txt_dir_var.set(str(data.parent / "textos"))
        if not self.sn_out_var.get():
            self.sn_out_var.set(str(self._new_data_dir(path) / Path(path).name))

    def _au_browse(self):
        path = filedialog.askdirectory(title="Carpeta data del juego")
        if path:
            self.au_src_var.set(path)
            if not self.au_out_var.get():
                self.au_out_var.set(str(Path(path).parent / "audio"))

    def _build_images_tab(self, tab):
        tab.grid_columnconfigure(1, weight=1)
        tab.grid_rowconfigure(5, weight=1)

        self.src_var = tk.StringVar()
        ctk.CTkLabel(tab, text="Carpeta data o .cpk:").grid(row=0, column=0, sticky="w", padx=(4, 8), pady=6)
        src_entry = ctk.CTkEntry(tab, textvariable=self.src_var)
        src_entry.grid(row=0, column=1, sticky="ew", pady=6)
        src_entry.bind("<Return>", lambda _e: self._update_images_info())
        src_entry.bind("<FocusOut>", lambda _e: self._update_images_info())
        btns = ctk.CTkFrame(tab, fg_color="transparent")
        btns.grid(row=0, column=2, padx=(8, 4), pady=6)
        b1 = ctk.CTkButton(btns, text="Carpeta…", width=80, command=self.browse_src_dir)
        b1.pack(side="left")
        b2 = ctk.CTkButton(btns, text=".cpk…", width=60, command=self.browse_src_cpk)
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
        cb1 = ctk.CTkCheckBox(opts, text="Reanudar (saltar lo ya extraído)", variable=self.resume_var)
        cb1.pack(side="left")
        ctk.CTkLabel(opts, text="Procesos:").pack(side="left", padx=(16, 6))
        cores = os.cpu_count() or 1
        self.workers_var = tk.StringVar(value=str(yeti_extract.default_workers()))
        om = ctk.CTkOptionMenu(opts, values=[str(n) for n in range(1, cores + 1)], width=70,
                               variable=self.workers_var)
        om.pack(side="left")
        self.busy_widgets += [cb1, om]

        btn = ctk.CTkButton(tab, text="Extraer imágenes", width=160, command=self.start_images)
        btn.grid(row=4, column=2, sticky="e", padx=4, pady=(6, 0))
        self.busy_widgets.append(btn)
        native = "CRILAYLA nativo" if yeti_codecs.native_available() else \
            f"Sin {yeti_codecs.LIB_NAME}: CRILAYLA en Python (lento)"
        ctk.CTkLabel(tab, text=native, anchor="w", text_color=("gray35", "gray65")) \
            .grid(row=4, column=1, sticky="w", pady=(6, 0))

        self.img_preview = ctk.CTkLabel(tab, text="")
        self.img_preview.grid(row=5, column=0, columnspan=3, sticky="nsew", pady=8)

    def _build_cpk_tab(self, tab):
        tab.grid_columnconfigure(1, weight=1)
        tab.grid_rowconfigure(2, weight=1)

        self.cpk_var = tk.StringVar()
        ctk.CTkLabel(tab, text="Archivo .cpk:").grid(row=0, column=0, sticky="w", padx=(4, 8), pady=4)
        cpk_entry = ctk.CTkEntry(tab, textvariable=self.cpk_var)
        cpk_entry.grid(row=0, column=1, sticky="ew", pady=4)
        cpk_entry.bind("<Return>", lambda _e: self.load_cpk(self.cpk_var.get()))
        btn = ctk.CTkButton(tab, text="Abrir…", width=100, command=self.browse_cpk)
        btn.grid(row=0, column=2, padx=(8, 4), pady=4)
        self.busy_widgets.append(btn)

        self.cpk_info = ctk.CTkLabel(tab, text="Ningún archivo abierto", anchor="w",
                                     text_color=("gray35", "gray65"))
        self.cpk_info.grid(row=1, column=0, columnspan=3, sticky="ew", padx=4)

        tree_frame = ctk.CTkFrame(tab, fg_color="transparent")
        tree_frame.grid(row=2, column=0, columnspan=3, sticky="nsew", padx=4, pady=4)
        tree_frame.grid_columnconfigure(0, weight=1)
        tree_frame.grid_rowconfigure(0, weight=1)

        self.tree = ttk.Treeview(tree_frame, columns=("size", "real", "comp"), selectmode="extended",
                                 style="Yeti.Treeview")
        self.tree.heading("#0", text="Archivo", anchor="w")
        self.tree.heading("size", text="Tamaño", anchor="e")
        self.tree.heading("real", text="Descomprimido", anchor="e")
        self.tree.heading("comp", text="Compresión", anchor="w")
        self.tree.column("#0", stretch=True, width=240)
        self.tree.column("size", stretch=False, width=110, anchor="e")
        self.tree.column("real", stretch=False, width=120, anchor="e")
        self.tree.column("comp", stretch=False, width=110, anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ctk.CTkScrollbar(tree_frame, command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._update_selection_label())

        self.raw_out_var = tk.StringVar()
        ctk.CTkLabel(tab, text="Destino:").grid(row=3, column=0, sticky="w", padx=(4, 8), pady=4)
        ctk.CTkEntry(tab, textvariable=self.raw_out_var).grid(row=3, column=1, sticky="ew", pady=4)
        btn = ctk.CTkButton(tab, text="Elegir…", width=100, command=self.browse_raw_out)
        btn.grid(row=3, column=2, padx=(8, 4), pady=4)
        self.busy_widgets.append(btn)

        actions = ctk.CTkFrame(tab, fg_color="transparent")
        actions.grid(row=4, column=0, columnspan=3, sticky="ew", padx=4, pady=(4, 0))
        self.sel_label = ctk.CTkLabel(actions, text="", text_color=("gray35", "gray65"))
        self.sel_label.pack(side="left")
        btn_all = ctk.CTkButton(actions, text="Extraer todo", command=lambda: self.start_raw(False))
        btn_all.pack(side="right")
        btn_sel = ctk.CTkButton(actions, text="Extraer seleccionados",
                                command=lambda: self.start_raw(True))
        btn_sel.pack(side="right", padx=8)
        btn_pack = ctk.CTkButton(actions, text="Reempaquetar carpeta…", fg_color="gray40",
                                 hover_color="gray30", command=self.start_pack_files)
        btn_pack.pack(side="right", padx=(0, 8))
        self.busy_widgets += [btn_all, btn_sel, btn_pack]

        ctk.CTkLabel(tab, text="Los archivos se guardan ya descomprimidos (sin CRILAYLA ni LZSS), "
                               "como <id>.bin.", anchor="w", text_color=("gray35", "gray65")) \
            .grid(row=5, column=0, columnspan=3, sticky="ew", padx=4, pady=(4, 0))

    def _build_xtx_tab(self, tab):
        tab.grid_columnconfigure(1, weight=1)
        tab.grid_rowconfigure(4, weight=1)

        self.xtx_var = tk.StringVar()
        ctk.CTkLabel(tab, text="Archivo .xtx:").grid(row=0, column=0, sticky="w", padx=(4, 8), pady=6)
        xtx_entry = ctk.CTkEntry(tab, textvariable=self.xtx_var)
        xtx_entry.grid(row=0, column=1, sticky="ew", pady=6)
        xtx_entry.bind("<Return>", lambda _e: self.load_xtx(self.xtx_var.get()))
        btn = ctk.CTkButton(tab, text="Abrir…", width=120, command=self.browse_xtx)
        btn.grid(row=0, column=2, padx=(8, 4), pady=6)
        self.busy_widgets.append(btn)

        self.xtx_info = ctk.CTkLabel(tab, text="", anchor="w", text_color=("gray35", "gray65"))
        self.xtx_info.grid(row=1, column=1, columnspan=2, sticky="ew")

        self.xtx_out_var = tk.StringVar()
        ctk.CTkLabel(tab, text="PNG de salida:").grid(row=2, column=0, sticky="w", padx=(4, 8), pady=6)
        ctk.CTkEntry(tab, textvariable=self.xtx_out_var).grid(row=2, column=1, sticky="ew", pady=6)
        btn = ctk.CTkButton(tab, text="Guardar como…", width=120, command=self.browse_xtx_out)
        btn.grid(row=2, column=2, padx=(8, 4), pady=6)
        self.busy_widgets.append(btn)

        row = ctk.CTkFrame(tab, fg_color="transparent")
        row.grid(row=3, column=1, columnspan=2, sticky="ew", pady=6)
        self.split_var = tk.BooleanVar(value=False)
        cb = ctk.CTkCheckBox(row, text="Un PNG por canal (_a, _r, _g, _b; font48.xtx guarda una hoja "
                                       "de glyphs en cada canal)", variable=self.split_var)
        cb.pack(side="left")
        btn = ctk.CTkButton(row, text="Convertir", width=120, command=self.start_xtx)
        btn.pack(side="right", padx=(8, 4))
        self.busy_widgets += [cb, btn]

        self.xtx_preview = ctk.CTkLabel(tab, text="")
        self.xtx_preview.grid(row=4, column=0, columnspan=3, sticky="nsew", pady=8)

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
        style.configure("Yeti.Treeview", background=bg, fieldbackground=bg, foreground=fg,
                        rowheight=24, borderwidth=0)
        style.map("Yeti.Treeview", background=[("selected", sel)], foreground=[("selected", "#ffffff")])
        style.configure("Yeti.Treeview.Heading", background=head_bg, foreground=fg, relief="flat")
        style.map("Yeti.Treeview.Heading", background=[("active", head_bg)])

    # ------------------------------------------------------ Pestaña Imágenes

    def browse_src_dir(self):
        path = filedialog.askdirectory(title="Carpeta data del juego")
        if path:
            self._set_src(path)

    def browse_src_cpk(self):
        path = filedialog.askopenfilename(title="Abrir archivo .cpk",
                                          filetypes=[("Archivos CPK", "*.cpk"), ("Todos", "*.*")])
        if path:
            self._set_src(path)

    def _set_src(self, path):
        self.src_var.set(path)
        if not self.img_out_var.get():
            # Junto a la carpeta data, nunca dentro
            p = Path(path)
            data_dir = p.parent if p.is_file() else p
            self.img_out_var.set(str(data_dir.parent / "png"))
        self._update_images_info()

    def _update_images_info(self):
        src = self.src_var.get().strip().strip('"')
        if not src or not Path(src).exists():
            self.src_info.configure(text="")
            return
        try:
            cpks = yeti_extract.find_cpks(src)
        except OSError as e:
            self.src_info.configure(text=str(e))
            return
        names = ", ".join(p.name for p in cpks) or "ningún .cpk de imágenes"
        self.src_info.configure(text=f"Se procesará: {names}")

    def browse_img_out(self):
        path = filedialog.askdirectory(title="Carpeta de destino")
        if path:
            self.img_out_var.set(path)

    def start_images(self):
        src = self.src_var.get().strip().strip('"')
        out_dir = self.img_out_var.get().strip()
        if not src or not Path(src).exists():
            messagebox.showwarning(APP_TITLE, "Elige la carpeta data del juego o un archivo .cpk.")
            return
        if not out_dir:
            messagebox.showwarning(APP_TITLE, "Elige una carpeta de destino.")
            return
        if not yeti_extract.find_cpks(src):
            messagebox.showwarning(APP_TITLE, "No hay archivos .cpk que procesar.")
            return

        def done(r):
            text = f"{r.images} imágenes y {r.others} otros archivos en:\n{out_dir}"
            if r.skipped:
                text += f"\n({r.skipped} archivos ya estaban hechos)"
            if r.errors:
                text += f"\n\n{len(r.errors)} errores:\n" + "\n".join(r.errors[:10])
            if messagebox.askyesno(APP_TITLE, text + "\n\n¿Abrir la carpeta?"):
                open_folder(out_dir)

        self.img_preview.configure(image=None, text="")
        self.run_task(yeti_extract.extract_images, (src, out_dir),
                      {"resume": self.resume_var.get(),
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

    # -------------------------------------------------- Pestaña Archivos CPK

    def browse_cpk(self):
        path = filedialog.askopenfilename(title="Abrir archivo .cpk",
                                          filetypes=[("Archivos CPK", "*.cpk"), ("Todos", "*.*")])
        if path:
            self.load_cpk(path)

    def load_cpk(self, path):
        path = path.strip().strip('"')
        if not path:
            return
        try:
            with CPK(path) as cpk:
                entries, mode = cpk.entries, cpk.mode
        except Exception as e:
            messagebox.showerror(APP_TITLE, f"No se pudo leer el archivo:\n{e}")
            return
        self.cpk_var.set(path)
        self.entries = entries
        p = Path(path)
        self.raw_out_var.set(str(p.parent / p.stem))

        total = sum(e.size for e in entries)
        packed = sum(1 for e in entries if e.compressed)
        self.cpk_info.configure(text=f"CpkMode {mode}   ·   {len(entries)} archivos   ·   "
                                     f"{format_size(total)}   ·   {packed} con CRILAYLA")
        self.tree.delete(*self.tree.get_children())
        for i, e in enumerate(entries):
            self.tree.insert("", "end", iid=str(i), text=e.path,
                             values=(format_size(e.size), format_size(e.extract_size),
                                     "CRILAYLA" if e.compressed else ""))
        self._update_selection_label()

    def _update_selection_label(self):
        if not self.entries:
            self.sel_label.configure(text="")
            return
        self.sel_label.configure(text=f"{len(self.entries)} archivos · "
                                      f"{len(self.tree.selection())} seleccionados")

    def browse_raw_out(self):
        path = filedialog.askdirectory(title="Carpeta de destino")
        if path:
            self.raw_out_var.set(path)

    def start_raw(self, only_selected):
        cpk_path = self.cpk_var.get().strip()
        out_dir = self.raw_out_var.get().strip()
        if not self.entries:
            messagebox.showwarning(APP_TITLE, "Primero abre un archivo .cpk.")
            return
        if not out_dir:
            messagebox.showwarning(APP_TITLE, "Elige una carpeta de destino.")
            return
        ids = None
        if only_selected:
            ids = [self.entries[int(iid)].id for iid in self.tree.selection()]
            if not ids:
                messagebox.showwarning(APP_TITLE, "No hay archivos seleccionados.")
                return

        def done(count):
            if messagebox.askyesno(APP_TITLE, f"Se extrajeron {count} archivos en:\n{out_dir}\n\n"
                                              "¿Abrir la carpeta?"):
                open_folder(out_dir)

        self.run_task(yeti_extract.extract_raw, (cpk_path, out_dir), {"ids": ids}, "Extrayendo", done)

    # ----------------------------------------------------------- Pestaña XTX

    def browse_xtx(self):
        path = filedialog.askopenfilename(title="Abrir textura .xtx",
                                          filetypes=[("Texturas XTX", "*.xtx"), ("Todos", "*.*")])
        if path:
            self.load_xtx(path)

    def load_xtx(self, path):
        path = path.strip().strip('"')
        if not path:
            return
        try:
            data = Path(path).read_bytes()
            fmt, w, h = xtx_info(data)
            img = xtx_to_image(data)
        except Exception as e:
            messagebox.showerror(APP_TITLE, f"No se pudo leer la textura:\n{e}")
            return
        self.xtx_var.set(path)
        self.xtx_out_var.set(str(Path(path).with_suffix(".png")))
        names = {0: "ARGB8888", 1: "ARGB4444", 2: "DXT5"}
        self.xtx_info.configure(text=f"{w}x{h}   ·   formato {fmt} ({names.get(fmt, '?')})")
        self.xtx_preview.configure(image=preview_image(img), text="")

    def browse_xtx_out(self):
        initial = Path(self.xtx_out_var.get() or "textura.png")
        path = filedialog.asksaveasfilename(title="Guardar PNG", defaultextension=".png",
                                            initialdir=str(initial.parent) if initial.parent.exists() else None,
                                            initialfile=initial.name,
                                            filetypes=[("PNG", "*.png")])
        if path:
            self.xtx_out_var.set(path)

    def start_xtx(self):
        xtx_path = self.xtx_var.get().strip()
        out_path = self.xtx_out_var.get().strip()
        if not xtx_path or not Path(xtx_path).is_file():
            messagebox.showwarning(APP_TITLE, "Primero abre una textura .xtx.")
            return
        if not out_path:
            messagebox.showwarning(APP_TITLE, "Elige el PNG de salida.")
            return

        def convert(xtx, out, split, progress=None, cancel=None):
            return yeti_extract.xtx_file_to_png(xtx, out, split)

        def done(paths):
            if messagebox.askyesno(APP_TITLE, "Guardado:\n" + "\n".join(str(p) for p in paths) +
                                   "\n\n¿Abrir la carpeta?"):
                open_folder(Path(out_path).parent)

        self.run_task(convert, (xtx_path, out_path, self.split_var.get()), {}, "Convirtiendo", done)

    def start_pack_files(self):
        cpk_path = self.cpk_var.get().strip()
        if not self.entries:
            messagebox.showwarning(APP_TITLE, "Primero abre el .cpk original.")
            return
        folder = filedialog.askdirectory(title="Carpeta con los archivos <id>.bin o <id>.hca")
        if not folder:
            return
        out = filedialog.asksaveasfilename(title="Guardar .cpk", defaultextension=".cpk",
                                           initialdir=str(self._new_data_dir(cpk_path)),
                                           initialfile=Path(cpk_path).name,
                                           filetypes=[("Archivos CPK", "*.cpk"), ("Todos", "*.*")])
        if not out or not self._confirm_overwrite(out, cpk_path):
            return
        self.run_task(yeti_extract.pack_files, (cpk_path, folder, out), {}, "Reempaquetando",
                      lambda r: self._pack_done(r, out))

    def _pack_done(self, r, out):
        if r.errors:
            messagebox.showerror(APP_TITLE, "No se creó el archivo. Errores:\n\n" + "\n".join(r.errors[:15]))
            return
        changed = "\n".join(r.changed[:15]) + ("\n…" if len(r.changed) > 15 else "")
        if messagebox.askyesno(APP_TITLE, f"{len(r.changed)} reemplazados de {r.entries} archivos"
                                          f"{':' if r.changed else '.'}\n{changed}\n\n{out}\n\n"
                                          "¿Abrir la carpeta?"):
            open_folder(Path(out).parent)

    # ------------------------------------------------- Pestaña Reempaquetar

    def start_repack(self):
        cpk, folder, out = (v.get().strip() for v in (self.rp_cpk_var, self.rp_dir_var, self.rp_out_var))
        if not cpk or not Path(cpk).is_file():
            messagebox.showwarning(APP_TITLE, "Elige el .cpk original.")
            return
        if not folder or not Path(folder).is_dir():
            messagebox.showwarning(APP_TITLE, "Elige la carpeta con las imágenes extraídas.")
            return
        if not out or not self._confirm_overwrite(out, cpk):
            return
        self.run_task(yeti_extract.pack_images, (cpk, folder, out), {}, "Reempaquetando",
                      lambda r: self._pack_done(r, out))

    # ---------------------------------------------------- Pestaña Guiones

    def _load_charmap(self):
        path = self.charmap_var.get().strip()
        return yeti_script.CharMap.load(path) if path else None

    def _show_report(self, text):
        self.script_report.configure(state="normal")
        self.script_report.delete("1.0", "end")
        self.script_report.insert("1.0", text)
        self.script_report.configure(state="disabled")

    def start_script_strings(self):
        sn, folder = self.sn_var.get().strip(), self.txt_dir_var.get().strip()
        if not sn or not Path(sn).is_file():
            messagebox.showwarning(APP_TITLE, "Elige sn.bin.")
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

        self.run_task(yeti_script.extract_strings, (sn, folder),
                      {"charmap": charmap, "with_listing": self.listing_var.get()},
                      "Extrayendo textos", done)

    def start_script_rebuild(self):
        sn, folder, out = (v.get().strip() for v in (self.sn_var, self.txt_dir_var, self.sn_out_var))
        if not sn or not Path(sn).is_file():
            messagebox.showwarning(APP_TITLE, "Elige sn.bin.")
            return
        if not folder or not Path(folder).is_dir():
            messagebox.showwarning(APP_TITLE, "Elige la carpeta de textos.")
            return
        if not out or not self._confirm_overwrite(out, sn):
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
                if yeti_script.ST_CUT in e.flags or yeti_script.ST_UNSUPPORTED in e.flags \
                        or yeti_script.ST_REPLACED in e.flags:
                    lines.append(f"[{', '.join(e.flags)}] {e.script} {e.sid:04d}: {e.detail}")
            self._show_report("\n".join(lines))

        self.run_task(yeti_script.rebuild, (sn, folder, out),
                      {"charmap": charmap, "mode": "fixed" if self.fixed_var.get() else "reloc"},
                      "Reconstruyendo guiones", done)

    # ------------------------------------------------------ Pestaña Audio

    def start_audio(self):
        src, out = self.au_src_var.get().strip(), self.au_out_var.get().strip()
        if not src or not Path(src).exists():
            messagebox.showwarning(APP_TITLE, "Elige la carpeta data del juego.")
            return
        if not out:
            messagebox.showwarning(APP_TITLE, "Elige una carpeta de destino.")
            return

        def done(r):
            text = f"{r.files} archivos .hca" + (f" y {r.wav} .wav" if r.wav else "") + f" en:\n{out}"
            if r.errors:
                text += f"\n\n{len(r.errors)} errores al convertir:\n" + "\n".join(r.errors[:5])
            if messagebox.askyesno(APP_TITLE, text + "\n\n¿Abrir la carpeta?"):
                open_folder(out)

        self.run_task(yeti_extract.extract_audio, (src, out), {"wav": self.wav_var.get()},
                      "Extrayendo audio", done)

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
        self.status.configure(text="Cancelando… (se terminan los archivos en curso)")

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
