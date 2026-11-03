#!/usr/bin/env python3
"""
FVP Tools GUI - Interfaz gráfica para Favorite View Point System
Archivos BIN, imágenes NVSG y scripts HCB.
"""

import os
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk
from PIL import Image

import fvp_bin
import fvp_hcb
import fvp_nvsg
from fvp_common import Cancelled

APP_TITLE = "FVP Tools"
PREVIEW_SIZE = (300, 260)
MAX_REPORT_ROWS = 5000

MODE_RELOC = "Relocate (no limit)"
MODE_FIXED = "Fixed size"

REPORT_FILTERS = {
    "With warnings": lambda e: fvp_hcb.ST_CUT in e.flags or fvp_hcb.ST_UNSUPPORTED in e.flags,
    "Grew": lambda e: fvp_hcb.ST_GREW in e.flags,
    "Cut": lambda e: fvp_hcb.ST_CUT in e.flags,
    "Replaced characters": lambda e: fvp_hcb.ST_REPLACED in e.flags,
    "Unsupported characters": lambda e: fvp_hcb.ST_UNSUPPORTED in e.flags,
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
        self._preview_refs = {}

        self.bin_entries = []
        self.report = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.tabs = ctk.CTkTabview(self)
        self.tabs.grid(row=0, column=0, sticky="nsew", padx=12, pady=(6, 0))
        self._build_bin_tab(self.tabs.add("BIN Files"))
        self._build_nvsg_tab(self.tabs.add("NVSG Images"))
        self._build_hcb_tab(self.tabs.add("HCB Scripts"))
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
        tree = ttk.Treeview(frame, columns=columns[1:], selectmode="extended", style="FVP.Treeview")
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

    def _make_preview(self, master, key):
        frame = ctk.CTkFrame(master, width=PREVIEW_SIZE[0] + 20)
        frame.grid_propagate(False)
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(0, weight=1)
        img_label = ctk.CTkLabel(frame, text="No preview")
        img_label.grid(row=0, column=0, sticky="nsew", padx=10, pady=(10, 4))
        info = ctk.CTkLabel(frame, text="", justify="left", anchor="w")
        info.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 10))
        self._preview_refs[key] = (img_label, info, None)
        return frame

    def _show_preview(self, key, img=None, text=""):
        img_label, info, _ = self._preview_refs[key]
        ctk_img = None
        if img is not None:
            thumb = img.copy()
            if thumb.mode not in ("RGB", "RGBA"):
                thumb = thumb.convert("RGBA")
            thumb.thumbnail(PREVIEW_SIZE)
            ctk_img = ctk.CTkImage(light_image=thumb, dark_image=thumb, size=thumb.size)
            img_label.configure(image=ctk_img, text="")
        else:
            img_label.configure(image=None, text="No preview")
        info.configure(text=text)
        self._preview_refs[key] = (img_label, info, ctk_img)

    @staticmethod
    def _nvsg_info(h: fvp_nvsg.NVSGHeader) -> str:
        return (f"Format {h.fmt}: {fvp_nvsg.FORMAT_NAMES.get(h.fmt, '?')}\n"
                f"Size: {h.width}×{h.height}   Frames: {h.frames}\n"
                f"Position: x={h.x}  y={h.y}")

    def _preview_bytes(self, key, data: bytes, name=""):
        try:
            if fvp_nvsg.is_nvsg(data):
                img, h = fvp_nvsg.decode_bytes(data)
                self._show_preview(key, img, self._nvsg_info(h))
            elif data[:4] == b'\x89PNG' or data[:3] == b'\xff\xd8\xff':
                from io import BytesIO
                img = Image.open(BytesIO(data))
                self._show_preview(key, img, f"{name}\n{img.width}×{img.height} {img.mode}")
            else:
                kind = fvp_bin.detect_type(data) or "unknown"
                self._show_preview(key, None, f"Type: {kind}\n{format_size(len(data))}")
        except Exception as e:
            self._show_preview(key, None, f"Could not show it:\n{e}")

    # ------------------------------------------------------------ Pestaña BIN

    def _build_bin_tab(self, tab):
        tab.grid_columnconfigure(1, weight=1)
        tab.grid_rowconfigure(3, weight=1)

        self.bin_path = PathRow(tab, 0, ".bin file:", self.browse_bin, "Open…")
        self.bin_path.entry.bind("<Return>", lambda _e: self.load_bin(self.bin_path.get()))
        self._busy(self.bin_path.button)

        self.bin_info = muted(ctk.CTkLabel, tab, text="No file open", anchor="w")
        self.bin_info.grid(row=1, column=0, columnspan=3, sticky="ew", padx=4)

        self.bin_filter = tk.StringVar()
        self.bin_filter.trace_add("write", lambda *_: self._refresh_bin_tree())
        ctk.CTkLabel(tab, text="Filter:").grid(row=2, column=0, sticky="w", padx=(4, 8), pady=3)
        ctk.CTkEntry(tab, textvariable=self.bin_filter, placeholder_text="name contains…") \
            .grid(row=2, column=1, columnspan=2, sticky="ew", padx=(0, 4), pady=3)

        body = ctk.CTkFrame(tab, fg_color="transparent")
        body.grid(row=3, column=0, columnspan=3, sticky="nsew", padx=4, pady=4)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)
        frame, self.bin_tree = self._make_tree(
            body, ("name", "size", "type"), ("Name", "Size", "Type"), (380, 100, 70),
            {"size": "e"})
        frame.grid(row=0, column=0, sticky="nsew")
        self.bin_tree.bind("<<TreeviewSelect>>", lambda _e: self._on_bin_select())
        self._make_preview(body, "bin").grid(row=0, column=1, sticky="ns", padx=(8, 0))

        self.bin_out = PathRow(tab, 4, "Output:", self.browse_bin_out)
        self._busy(self.bin_out.button)

        actions = ctk.CTkFrame(tab, fg_color="transparent")
        actions.grid(row=5, column=0, columnspan=3, sticky="ew", padx=4, pady=(2, 0))
        self.bin_auto_ext = tk.BooleanVar(value=True)
        self._busy(ctk.CTkCheckBox(actions, text="Add extensions (.ogg/.wav)",
                                   variable=self.bin_auto_ext)).pack(side="left")
        self.bin_sel_label = muted(ctk.CTkLabel, actions, text="")
        self.bin_sel_label.pack(side="left", padx=12)
        self._busy(ctk.CTkButton(actions, text="Extract all",
                                 command=lambda: self.start_bin_extract(False))).pack(side="right")
        self._busy(ctk.CTkButton(actions, text="Extract selected",
                                 command=lambda: self.start_bin_extract(True))).pack(side="right", padx=8)

        pack = ctk.CTkFrame(tab)
        pack.grid(row=6, column=0, columnspan=3, sticky="ew", padx=4, pady=(10, 0))
        pack.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(pack, text="Pack", font=ctk.CTkFont(weight="bold")) \
            .grid(row=0, column=0, sticky="w", padx=8, pady=(6, 0))
        self.bin_pack_in = PathRow(pack, 1, "Folder:", self.browse_bin_pack_in)
        self.bin_pack_out = PathRow(pack, 2, ".bin file:", self.browse_bin_pack_out, "Save as…")
        self._busy(self.bin_pack_in.button, self.bin_pack_out.button)
        self._busy(ctk.CTkButton(pack, text="Pack", width=140, command=self.start_bin_pack)) \
            .grid(row=3, column=2, sticky="e", padx=4, pady=(2, 8))

    def browse_bin(self):
        path = filedialog.askopenfilename(title="Open .bin file",
                                          filetypes=[("BIN files", "*.bin"), ("All", "*.*")])
        if path:
            self.load_bin(path)

    def load_bin(self, path):
        path = path.strip().strip('"')
        if not path:
            return
        try:
            entries = fvp_bin.read_index(path)
            with open(path, 'rb') as f:
                kinds = []
                for e in entries:
                    f.seek(e.offset)
                    kinds.append(fvp_bin.detect_type(f.read(16)))
        except Exception as e:
            messagebox.showerror(APP_TITLE, f"Could not read the file:\n{e}")
            return
        self.bin_path.set(path)
        self.bin_entries = list(zip(entries, kinds))
        p = Path(path)
        self.bin_out.set(p.parent / p.stem)
        # Los gráficos NVSG no deben llevar extensión para poder reempaquetarlos
        self.bin_auto_ext.set('NVSG' not in kinds)

        counts = {}
        for k in kinds:
            counts[k or "other"] = counts.get(k or "other", 0) + 1
        total = sum(e.size for e in entries)
        types = ", ".join(f"{n} {k}" for k, n in sorted(counts.items()))
        self.bin_info.configure(text=f"{len(entries)} files  ·  {format_size(total)}  ·  {types}")
        self._refresh_bin_tree()
        self._show_preview("bin")

    def _refresh_bin_tree(self):
        self.bin_tree.delete(*self.bin_tree.get_children())
        text = self.bin_filter.get().lower()
        for e, kind in self.bin_entries:
            if text in e.name.lower():
                self.bin_tree.insert("", "end", iid=str(e.index), text=f"{e.index:04d}_{e.name}",
                                     values=(format_size(e.size), kind))
        self._update_bin_sel()

    def _update_bin_sel(self):
        if not self.bin_entries:
            self.bin_sel_label.configure(text="")
            return
        self.bin_sel_label.configure(
            text=f"{len(self.bin_tree.get_children())} shown · {len(self.bin_tree.selection())} selected")

    def _on_bin_select(self):
        self._update_bin_sel()
        sel = self.bin_tree.selection()
        if len(sel) != 1:
            return
        entry, _ = self.bin_entries[int(sel[0])]
        try:
            data = fvp_bin.read_entry(self.bin_path.get(), entry)
        except OSError as e:
            self._show_preview("bin", None, str(e))
            return
        self._preview_bytes("bin", data, entry.name)

    def browse_bin_out(self):
        path = filedialog.askdirectory(title="Output folder")
        if path:
            self.bin_out.set(path)

    def start_bin_extract(self, only_selected):
        if not self.bin_entries:
            messagebox.showwarning(APP_TITLE, "Open a .bin file first.")
            return
        out_dir = self.bin_out.get()
        if not out_dir:
            messagebox.showwarning(APP_TITLE, "Choose an output folder.")
            return
        indices = None
        if only_selected:
            indices = [int(i) for i in self.bin_tree.selection()]
            if not indices:
                messagebox.showwarning(APP_TITLE, "No files selected.")
                return

        def done(count):
            if not self.bin_pack_in.get():
                self.bin_pack_in.set(out_dir)
            self._ask_open(f"Extracted {count} files to:\n{out_dir}", out_dir)

        self.run_task(fvp_bin.extract, (self.bin_path.get(), out_dir),
                      {"auto_ext": self.bin_auto_ext.get(), "indices": indices}, "Extracting", done)

    def browse_bin_pack_in(self):
        path = filedialog.askdirectory(title="Folder with the files to pack")
        if path:
            self.bin_pack_in.set(path)
            if not self.bin_pack_out.get():
                p = Path(path)
                self.bin_pack_out.set(p.parent / f"{p.name}_new.bin")

    def browse_bin_pack_out(self):
        path = self._ask_save("Save .bin file", self.bin_pack_out.get() or "new.bin",
                              ".bin", [("BIN files", "*.bin"), ("All", "*.*")])
        if path:
            self.bin_pack_out.set(path)

    def start_bin_pack(self):
        in_dir, out_file = self.bin_pack_in.get(), self.bin_pack_out.get()
        if not in_dir or not Path(in_dir).is_dir():
            messagebox.showwarning(APP_TITLE, "Choose a valid input folder.")
            return
        if not self._check_output(out_file, in_dir):
            return
        self.run_task(fvp_bin.pack, (in_dir, out_file), {}, "Packing",
                      lambda n: self._ask_open(f"File created ({n} files):\n{out_file}",
                                               Path(out_file).parent))

    # ----------------------------------------------------------- Pestaña NVSG

    def _build_nvsg_tab(self, tab):
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(2, weight=1)

        dec = ctk.CTkFrame(tab)
        dec.grid(row=0, column=0, sticky="ew", padx=4, pady=(4, 6))
        dec.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(dec, text="NVSG → PNG", font=ctk.CTkFont(weight="bold")) \
            .grid(row=0, column=0, sticky="w", padx=8, pady=(6, 0))
        self.nv_dec_in = PathRow(dec, 1, "NVSG folder:", self.browse_nv_dec_in)
        self.nv_dec_out = PathRow(dec, 2, "PNG folder:", lambda: self._browse_dir(self.nv_dec_out))
        self._busy(self.nv_dec_in.button, self.nv_dec_out.button)
        self.nv_dec_in.entry.bind("<Return>", lambda _e: self._list_nvsg_folder(self.nv_dec_in.get()))
        muted(ctk.CTkLabel, dec, text=f"Writes {fvp_nvsg.LOG_NAME} with the data needed to encode again.") \
            .grid(row=3, column=1, sticky="w")
        self._busy(ctk.CTkButton(dec, text="Decode", width=140, command=self.start_nv_decode)) \
            .grid(row=3, column=2, sticky="e", padx=4, pady=(2, 8))

        enc = ctk.CTkFrame(tab)
        enc.grid(row=1, column=0, sticky="ew", padx=4, pady=(0, 6))
        enc.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(enc, text="PNG → NVSG", font=ctk.CTkFont(weight="bold")) \
            .grid(row=0, column=0, sticky="w", padx=8, pady=(6, 0))
        self.nv_enc_in = PathRow(enc, 1, "PNG folder:", self.browse_nv_enc_in)
        self.nv_enc_log = PathRow(enc, 2, "decode_log.txt:", self.browse_nv_log)
        self.nv_enc_out = PathRow(enc, 3, "NVSG folder:", lambda: self._browse_dir(self.nv_enc_out))
        self._busy(self.nv_enc_in.button, self.nv_enc_log.button, self.nv_enc_out.button)
        self._busy(ctk.CTkButton(enc, text="Encode", width=140, command=self.start_nv_encode)) \
            .grid(row=4, column=2, sticky="e", padx=4, pady=(2, 8))

        body = ctk.CTkFrame(tab, fg_color="transparent")
        body.grid(row=2, column=0, sticky="nsew", padx=4, pady=4)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)
        frame, self.nv_tree = self._make_tree(body, ("name", "size"), ("Files in the folder", "Size"),
                                              (380, 100), {"size": "e"})
        self.nv_tree.configure(selectmode="browse")
        frame.grid(row=0, column=0, sticky="nsew")
        self.nv_tree.bind("<<TreeviewSelect>>", lambda _e: self._on_nv_select())
        self._make_preview(body, "nvsg").grid(row=0, column=1, sticky="ns", padx=(8, 0))
        self._nv_folder = None

    def _browse_dir(self, row: PathRow):
        path = filedialog.askdirectory()
        if path:
            row.set(path)

    def browse_nv_dec_in(self):
        path = filedialog.askdirectory(title="Folder with NVSG files")
        if path:
            self.nv_dec_in.set(path)
            if not self.nv_dec_out.get():
                self.nv_dec_out.set(Path(path).parent / f"{Path(path).name}_png")
            self._list_nvsg_folder(path)

    def browse_nv_enc_in(self):
        path = filedialog.askdirectory(title="Folder with the PNG files")
        if path:
            self.nv_enc_in.set(path)
            log = Path(path) / fvp_nvsg.LOG_NAME
            if log.exists() and not self.nv_enc_log.get():
                self.nv_enc_log.set(log)
            if not self.nv_enc_out.get():
                self.nv_enc_out.set(Path(path).parent / f"{Path(path).name}_nvsg")
            self._list_nvsg_folder(path)

    def browse_nv_log(self):
        path = filedialog.askopenfilename(title=f"Open {fvp_nvsg.LOG_NAME}",
                                          filetypes=[("Text", "*.txt"), ("All", "*.*")])
        if path:
            self.nv_enc_log.set(path)

    def _list_nvsg_folder(self, folder):
        self.nv_tree.delete(*self.nv_tree.get_children())
        self._nv_folder = Path(folder) if folder else None
        if not self._nv_folder or not self._nv_folder.is_dir():
            return
        for f in sorted(self._nv_folder.iterdir()):
            if f.is_file() and not f.name.startswith('_') and f.name != fvp_nvsg.LOG_NAME:
                self.nv_tree.insert("", "end", iid=f.name, text=f.name, values=(format_size(f.stat().st_size),))

    def _on_nv_select(self):
        sel = self.nv_tree.selection()
        if sel and self._nv_folder:
            try:
                data = (self._nv_folder / sel[0]).read_bytes()
            except OSError as e:
                self._show_preview("nvsg", None, str(e))
                return
            self._preview_bytes("nvsg", data, sel[0])

    def start_nv_decode(self):
        in_dir, out_dir = self.nv_dec_in.get(), self.nv_dec_out.get()
        if not in_dir or not Path(in_dir).is_dir() or not out_dir:
            messagebox.showwarning(APP_TITLE, "Choose the NVSG folder and the PNG folder.")
            return

        def done(result):
            count, errors = result
            self.nv_enc_in.set(out_dir)
            self.nv_enc_log.set(Path(out_dir) / fvp_nvsg.LOG_NAME)
            msg = f"Decoded {count} images to:\n{out_dir}"
            if errors:
                msg += f"\n\n{len(errors)} files are not NVSG or failed:\n" + "\n".join(errors[:8])
            self._ask_open(msg, out_dir)

        self.run_task(fvp_nvsg.batch_decode, (in_dir, out_dir), {}, "Decoding", done)

    def start_nv_encode(self):
        in_dir, log, out_dir = self.nv_enc_in.get(), self.nv_enc_log.get(), self.nv_enc_out.get()
        if not in_dir or not Path(in_dir).is_dir() or not out_dir:
            messagebox.showwarning(APP_TITLE, "Choose the PNG folder and the output folder.")
            return
        if not log or not Path(log).is_file():
            messagebox.showwarning(APP_TITLE, f"Choose the {fvp_nvsg.LOG_NAME} file.")
            return

        def done(result):
            count, missing = result
            msg = f"Encoded {count} images to:\n{out_dir}"
            if missing:
                msg += f"\n\n{len(missing)} PNG files without a log entry (skipped):\n" + "\n".join(missing[:8])
            self._ask_open(msg, out_dir)

        self.run_task(fvp_nvsg.batch_encode, (in_dir, out_dir, log), {}, "Encoding", done)

    # ------------------------------------------------------------ Pestaña HCB

    def _build_hcb_tab(self, tab):
        tab.grid_columnconfigure(1, weight=1)
        tab.grid_rowconfigure(7, weight=1)

        self.hcb_path = PathRow(tab, 0, ".hcb script:", self.browse_hcb, "Open…")
        self.hcb_strings = PathRow(tab, 1, "Text file:", self.browse_hcb_strings)
        self.hcb_charmap = PathRow(tab, 2, "Character table:", self.browse_charmap)
        self._busy(self.hcb_path.button, self.hcb_strings.button, self.hcb_charmap.button)

        ext = ctk.CTkFrame(tab)
        ext.grid(row=3, column=0, columnspan=3, sticky="ew", padx=4, pady=(6, 4))
        ctk.CTkLabel(ext, text="Extract", font=ctk.CTkFont(weight="bold")).pack(side="left", padx=8, pady=6)
        self.hcb_decompile = tk.BooleanVar(value=False)
        self.hcb_reverse = tk.BooleanVar(value=False)
        self._busy(ctk.CTkCheckBox(ext, text="Also decompile", variable=self.hcb_decompile)) \
            .pack(side="left", padx=8)
        self._busy(ctk.CTkCheckBox(ext, text="Apply table in reverse (HCB already translated)",
                                   variable=self.hcb_reverse)).pack(side="left", padx=8)
        self._busy(ctk.CTkButton(ext, text="Extract text", width=140, command=self.start_hcb_extract)) \
            .pack(side="right", padx=8, pady=6)

        reb = ctk.CTkFrame(tab)
        reb.grid(row=4, column=0, columnspan=3, sticky="ew", padx=4, pady=(0, 4))
        reb.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(reb, text="Rebuild", font=ctk.CTkFont(weight="bold")) \
            .grid(row=0, column=0, sticky="w", padx=8, pady=(6, 0))
        self.hcb_out = PathRow(reb, 1, "Output script:", self.browse_hcb_out, "Save as…")
        self._busy(self.hcb_out.button)
        ctk.CTkLabel(reb, text="Mode:").grid(row=2, column=0, sticky="w", padx=(4, 8), pady=3)
        self.hcb_mode = tk.StringVar(value=MODE_RELOC)
        self._busy(ctk.CTkSegmentedButton(reb, values=[MODE_RELOC, MODE_FIXED], variable=self.hcb_mode)) \
            .grid(row=2, column=1, sticky="w", pady=3)
        self._busy(ctk.CTkButton(reb, text="Rebuild", width=140, command=self.start_hcb_rebuild)) \
            .grid(row=2, column=2, sticky="e", padx=4, pady=(3, 8))
        muted(ctk.CTkLabel, reb, justify="left", anchor="w",
              text=f"Relocate: each text can grow up to {fvp_hcb.MAX_STRING_BYTES} bytes "
                   f"(≈{fvp_hcb.MAX_STRING_BYTES} unaccented letters, half as many Japanese characters).\n"
                   "Fixed size: the old behavior; texts longer than the original are cut.") \
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
            tab, ("id", "addr", "bytes", "state", "detail"),
            ("ID", "Address", "Bytes", "Status", "Detail"), (70, 100, 90, 200, 0))
        frame.grid(row=7, column=0, columnspan=3, sticky="nsew", padx=4, pady=4)

        tools = ctk.CTkFrame(tab, fg_color="transparent")
        tools.grid(row=8, column=0, columnspan=3, sticky="ew", padx=4)
        muted(ctk.CTkLabel, tools, text="Large projects:").pack(side="left")
        self._busy(ctk.CTkButton(tools, text="Split into parts…", width=140, command=self.hcb_split)) \
            .pack(side="left", padx=8)
        self._busy(ctk.CTkButton(tools, text="Merge parts…", width=120, command=self.hcb_merge)) \
            .pack(side="left")

    def browse_hcb(self):
        path = filedialog.askopenfilename(title="Open .hcb script",
                                          filetypes=[("HCB scripts", "*.hcb"), ("All", "*.*")])
        if path:
            self.hcb_path.set(path)
            p = Path(path)
            if not self.hcb_strings.get():
                self.hcb_strings.set(p.parent / f"{p.stem}_strings.txt")
            if not self.hcb_out.get():
                self.hcb_out.set(p.parent / f"{p.stem}_new.hcb")

    def browse_hcb_strings(self):
        path = filedialog.asksaveasfilename(
            title="Text file (an existing one to rebuild, a new one to extract)",
            defaultextension=".txt", confirmoverwrite=False,
            initialfile=Path(self.hcb_strings.get() or "strings.txt").name,
            filetypes=[("Text", "*.txt"), ("All", "*.*")])
        if path:
            self.hcb_strings.set(path)

    def browse_charmap(self):
        path = filedialog.askopenfilename(title="Character table",
                                          filetypes=[("Text", "*.txt"), ("All", "*.*")])
        if path:
            self.hcb_charmap.set(path)

    def browse_hcb_out(self):
        path = self._ask_save("Save .hcb script", self.hcb_out.get() or "new.hcb", ".hcb",
                              [("HCB scripts", "*.hcb"), ("All", "*.*")])
        if path:
            self.hcb_out.set(path)

    def _load_charmap(self):
        """Devuelve (ok, charmap)."""
        path = self.hcb_charmap.get()
        if not path:
            return True, None
        try:
            return True, fvp_hcb.CharMap.load(path)
        except Exception as e:
            messagebox.showerror(APP_TITLE, f"Error in the character table:\n{e}")
            return False, None

    def start_hcb_extract(self):
        hcb, strings = self.hcb_path.get(), self.hcb_strings.get()
        if not hcb or not Path(hcb).is_file():
            messagebox.showwarning(APP_TITLE, "Choose a .hcb script.")
            return
        if not strings:
            messagebox.showwarning(APP_TITLE, "Choose where to save the text file.")
            return
        if Path(strings).exists() and not messagebox.askyesno(
                APP_TITLE, f"{Path(strings).name} already exists and will be overwritten.\n"
                           "If it contains your translation, it will be lost. Continue?"):
            return
        ok, charmap = self._load_charmap()
        if not ok:
            return
        reverse = self.hcb_reverse.get()
        if reverse and not charmap:
            messagebox.showwarning(APP_TITLE, "Applying the table in reverse needs a character table.")
            return

        if self.hcb_decompile.get():
            out = Path(strings).with_name(Path(hcb).stem + "_decompiled.txt")
            fn, args = fvp_hcb.decompile, (hcb, out, strings)

            def done(result):
                self._ask_open(f"{result[1]} lines in:\n{strings}\n\nDecompiled ({result[0]} functions):\n{out}",
                               Path(strings).parent)
        else:
            fn, args = fvp_hcb.extract_strings, (hcb, strings)

            def done(count):
                self._ask_open(f"Extracted {count} lines to:\n{strings}", Path(strings).parent)

        self.run_task(fn, args, {"charmap": charmap, "reverse": reverse}, "Extracting text", done)

    def start_hcb_rebuild(self):
        hcb, strings, out = self.hcb_path.get(), self.hcb_strings.get(), self.hcb_out.get()
        if not hcb or not Path(hcb).is_file():
            messagebox.showwarning(APP_TITLE, "Choose the original .hcb script.")
            return
        if not strings or not Path(strings).is_file():
            messagebox.showwarning(APP_TITLE, "Choose the translated text file.")
            return
        if not out:
            messagebox.showwarning(APP_TITLE, "Choose the output script.")
            return
        if Path(out).resolve() == Path(hcb).resolve():
            messagebox.showwarning(APP_TITLE, "The output script cannot be the original.")
            return
        if Path(out).exists() and not messagebox.askyesno(APP_TITLE, f"{Path(out).name} already exists.\nOverwrite it?"):
            return
        ok, charmap = self._load_charmap()
        if not ok:
            return
        mode = 'reloc' if self.hcb_mode.get() == MODE_RELOC else 'fixed'

        def done(rep: fvp_hcb.RebuildReport):
            self.report = rep
            text = rep.summary() + f".  Size: {format_size(rep.original_size)} → {format_size(rep.new_size)}"
            if rep.warnings:
                text += "\n" + " · ".join(rep.warnings[:3])
            self.report_label.configure(text=text)
            self._refresh_report()
            problems = rep.count(fvp_hcb.ST_CUT) + rep.count(fvp_hcb.ST_UNSUPPORTED)
            msg = f"Script created:\n{out}\n\n{rep.summary()}"
            if problems:
                msg += f"\n\n{problems} lines with warnings: check the report."
            self._ask_open(msg, Path(out).parent)

        self.run_task(fvp_hcb.rebuild, (hcb, strings, out), {"charmap": charmap, "mode": mode},
                      "Rebuilding", done)

    def _refresh_report(self):
        self.report_tree.delete(*self.report_tree.get_children())
        if not self.report:
            return
        keep = REPORT_FILTERS[self.report_filter.get()]
        rows = [e for e in self.report.entries if keep(e)]
        for i, e in enumerate(rows[:MAX_REPORT_ROWS]):
            self.report_tree.insert("", "end", iid=str(i), text=f"{e.sid:04d}",
                                    values=(f"0x{e.addr:08X}", f"{e.old_len} → {e.new_len}",
                                            ", ".join(e.flags), e.detail))
        if len(rows) > MAX_REPORT_ROWS:
            self.report_tree.insert("", "end", iid="more", text="…",
                                    values=("", "", f"{len(rows) - MAX_REPORT_ROWS} more", ""))

    def hcb_split(self):
        path = filedialog.askopenfilename(title="Text file with <part> tags",
                                          filetypes=[("Text", "*.txt"), ("All", "*.*")])
        if not path:
            return
        try:
            messages = fvp_hcb.split_strings(path)
        except Exception as e:
            messagebox.showerror(APP_TITLE, str(e))
            return
        messagebox.showinfo(APP_TITLE, "\n".join(messages[:30]))

    def hcb_merge(self):
        build = filedialog.askopenfilename(title="Build script (list of parts)",
                                           filetypes=[("Text", "*.txt"), ("All", "*.*")])
        if not build:
            return
        out = filedialog.asksaveasfilename(title="Save merged text", defaultextension=".txt",
                                           initialfile="merged_strings.txt",
                                           filetypes=[("Text", "*.txt"), ("All", "*.*")])
        if not out:
            return
        try:
            count, messages = fvp_hcb.merge_strings(build, out)
        except Exception as e:
            messagebox.showerror(APP_TITLE, str(e))
            return
        self.hcb_strings.set(out)
        messagebox.showinfo(APP_TITLE, f"{count} lines merged into:\n{out}\n\n" + "\n".join(messages[:30]))

    # ------------------------------------------------------------ Comunes

    def _ask_save(self, title, current, ext, types):
        initial = Path(current)
        return filedialog.asksaveasfilename(
            title=title, defaultextension=ext, initialfile=initial.name,
            initialdir=str(initial.parent) if initial.parent.exists() else None, filetypes=types)

    def _check_output(self, out_file, in_dir=None):
        if not out_file:
            messagebox.showwarning(APP_TITLE, "Choose the output file.")
            return False
        out_path = Path(out_file).resolve()
        if in_dir and Path(in_dir).resolve() in out_path.parents:
            messagebox.showwarning(APP_TITLE, "The output file cannot be inside the input folder.")
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
        self.status = ctk.CTkLabel(bar, text="Ready", anchor="w")
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
        style.configure("FVP.Treeview", background=bg, fieldbackground=bg, foreground=fg,
                        rowheight=24, borderwidth=0)
        style.map("FVP.Treeview", background=[("selected", "#1f6aa5")], foreground=[("selected", "#ffffff")])
        style.configure("FVP.Treeview.Heading", background=head_bg, foreground=fg, relief="flat")
        style.map("FVP.Treeview.Heading", background=[("active", head_bg)])

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
                        counter = f" [{i + 1}/{total}]" if not name.endswith("…") else ""
                        self.status.configure(text=f"{self._task_label}{counter}  {name}")
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
