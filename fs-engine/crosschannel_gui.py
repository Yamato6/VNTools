#!/usr/bin/env python3
"""
CrossChannel MultiTool GUI - Interfaz gráfica para archivos .PD (FS Engine)
Juego: CROSS†CHANNEL
"""

import io
import os
import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk
from PIL import Image

import pd_core
from pd_core import Cancelled

APP_TITLE = "CrossChannel MultiTool"
PREVIEW_SIZE = (300, 260)
PREVIEW_INTERVAL = 0.5      # segundos entre actualizaciones de la vista previa al extraer
PREVIEW_MAX_SIZE = 64 << 20
MODE_AUTO = "Auto"


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


def read_entry(pd_file, mode: str, e: pd_core.PDEntry) -> bytes:
    with open(pd_file, 'rb') as f:
        f.seek(e.offset)
        return pd_core.apply_xor(f.read(e.size), mode)


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

        self.entries = []           # lista de pd_core.PDEntry del .PD abierto
        self.pd_mode = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.tabs = ctk.CTkTabview(self)
        self.tabs.grid(row=0, column=0, sticky="nsew", padx=12, pady=(6, 0))
        self._build_pd_tab(self.tabs.add("PD Files"))
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
        tree = ttk.Treeview(frame, columns=columns[1:], selectmode="extended", style="PD.Treeview")
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

    # ------------------------------------------------------------ Pestaña PD

    def _build_pd_tab(self, tab):
        tab.grid_columnconfigure(1, weight=1)
        tab.grid_rowconfigure(3, weight=1)

        self.pd_path = PathRow(tab, 0, ".PD file:", self.browse_pd, "Open…")
        self.pd_path.entry.bind("<Return>", lambda _e: self.load_pd(self.pd_path.get()))
        self._busy(self.pd_path.button)

        self.pd_info = muted(ctk.CTkLabel, tab, text="No file open", anchor="w")
        self.pd_info.grid(row=1, column=0, columnspan=3, sticky="ew", padx=4)

        self.pd_filter = tk.StringVar()
        self.pd_filter.trace_add("write", lambda *_: self._refresh_pd_tree())
        ctk.CTkLabel(tab, text="Filter:").grid(row=2, column=0, sticky="w", padx=(4, 8), pady=3)
        ctk.CTkEntry(tab, textvariable=self.pd_filter, placeholder_text="name contains…") \
            .grid(row=2, column=1, columnspan=2, sticky="ew", padx=(0, 4), pady=3)

        body = ctk.CTkFrame(tab, fg_color="transparent")
        body.grid(row=3, column=0, columnspan=3, sticky="nsew", padx=4, pady=4)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)
        frame, self.pd_tree = self._make_tree(body, ("name", "size"), ("Name", "Size"), (380, 100),
                                              {"size": "e"})
        frame.grid(row=0, column=0, sticky="nsew")
        self.pd_tree.bind("<<TreeviewSelect>>", lambda _e: self._on_pd_select())
        self.pd_preview = Preview(body)
        self.pd_preview.grid(row=0, column=1, sticky="ns", padx=(8, 0))

        self.pd_out = PathRow(tab, 4, "Output:", lambda: self._browse_dir(self.pd_out, "Output folder"))
        self._busy(self.pd_out.button)

        actions = ctk.CTkFrame(tab, fg_color="transparent")
        actions.grid(row=5, column=0, columnspan=3, sticky="ew", padx=4, pady=(2, 0))
        self.pd_sel_label = muted(ctk.CTkLabel, actions, text="")
        self.pd_sel_label.pack(side="left")
        self._busy(ctk.CTkButton(actions, text="Extract all", width=110,
                                 command=lambda: self.start_pd_extract(False))).pack(side="right")
        self._busy(ctk.CTkButton(actions, text="Extract selected", width=120,
                                 command=lambda: self.start_pd_extract(True))).pack(side="right", padx=8)

        pack = self._section(tab, 6, "Pack")
        self.pd_pack_in = PathRow(pack, 1, "Folder:", self.browse_pack_in)
        self.pd_pack_in.entry.bind("<FocusOut>", lambda _e: self._update_pack_info())
        self.pd_pack_out = PathRow(pack, 2, ".PD file:", self.browse_pack_out, "Save as…")
        self._busy(self.pd_pack_in.button, self.pd_pack_out.button)
        ctk.CTkLabel(pack, text="Mode:").grid(row=3, column=0, sticky="w", padx=(4, 8), pady=3)
        modes = ctk.CTkFrame(pack, fg_color="transparent")
        modes.grid(row=3, column=1, sticky="w")
        self.pack_mode = tk.StringVar(value=MODE_AUTO)
        self._busy(ctk.CTkSegmentedButton(modes, values=[MODE_AUTO, *pd_core.MODES], variable=self.pack_mode,
                                          command=lambda _v: self._update_pack_info())).pack(side="left")
        self.pack_info = muted(ctk.CTkLabel, modes, text="", anchor="w")
        self.pack_info.pack(side="left", padx=12)
        self._busy(ctk.CTkButton(pack, text="Pack", width=140, command=self.start_pd_pack)) \
            .grid(row=3, column=2, sticky="e", padx=4, pady=(2, 8))
        muted(ctk.CTkLabel, pack, anchor="w",
              text=f"An extracted folder keeps the original order and mode ({pd_core.INFO_FILE}). "
                   "PackPlus: data inverted with XOR 0xFF; PackOnly: data as is.") \
            .grid(row=4, column=0, columnspan=3, sticky="ew", padx=8, pady=(0, 6))
        self._update_pack_info()

    def browse_pd(self):
        path = filedialog.askopenfilename(title="Open .PD file", filetypes=[("PD files", "*.pd"), ("All", "*.*")])
        if path:
            self.load_pd(path)

    def load_pd(self, path):
        path = path.strip().strip('"')
        if not path:
            return
        try:
            mode, entries, warnings = pd_core.read_index(path)
        except Exception as e:
            messagebox.showerror(APP_TITLE, f"Could not read the file:\n{e}")
            return
        self.pd_path.set(path)
        self.entries = entries
        self.pd_mode = mode
        p = Path(path)
        self._auto(self.pd_out, p.parent / p.stem)
        self._auto(self.pd_pack_in, self.pd_out.get())
        self._auto(self.pd_pack_out, p.parent / f"{p.stem}_new.pd")
        self._update_pack_info()

        total = sum(e.size for e in entries)
        info = f"Mode: {mode}  ·  {len(entries)} files  ·  {format_size(total)}"
        if warnings:
            info += "  ·  " + "; ".join(warnings)
        self.pd_info.configure(text=info)
        self._refresh_pd_tree()
        self.pd_preview.show()

    def _refresh_pd_tree(self):
        self.pd_tree.delete(*self.pd_tree.get_children())
        text = self.pd_filter.get().lower()
        for i, e in enumerate(self.entries):
            if text in e.name.lower():
                self.pd_tree.insert("", "end", iid=str(i), text=e.name, values=(format_size(e.size),))
        self._update_pd_sel()

    def _update_pd_sel(self):
        if not self.entries:
            self.pd_sel_label.configure(text="")
            return
        self.pd_sel_label.configure(
            text=f"{len(self.pd_tree.get_children())} shown · {len(self.pd_tree.selection())} selected")

    def _on_pd_select(self):
        self._update_pd_sel()
        sel = self.pd_tree.selection()
        if len(sel) != 1:
            return
        e = self.entries[int(sel[0])]
        header = f"{e.name}\n{format_size(e.size)}"
        if e.size > PREVIEW_MAX_SIZE:
            self.pd_preview.show(header)
            return
        try:
            img = Image.open(io.BytesIO(read_entry(self.pd_path.get(), self.pd_mode, e)))
            img.load()
        except OSError:
            self.pd_preview.show(header)        # no es una imagen
            return
        self.pd_preview.show(header, [(img, f"{img.width}×{img.height} {img.mode}")])

    def start_pd_extract(self, only_selected):
        pd_file, out_dir = self.pd_path.get(), self.pd_out.get()
        if not self.entries:
            messagebox.showwarning(APP_TITLE, "Open a .PD file first.")
            return
        if not out_dir:
            messagebox.showwarning(APP_TITLE, "Choose an output folder.")
            return
        names = None
        if only_selected:
            names = [self.entries[int(i)].name for i in self.pd_tree.selection()]
            if not names:
                messagebox.showwarning(APP_TITLE, "No files selected.")
                return

        def done(result):
            _mode, count = result
            self._auto(self.pd_pack_in, out_dir)
            self._update_pack_info()
            self._ask_open(f"Extracted {count} files to:\n{out_dir}", out_dir)

        self.run_task(pd_core.extract, (pd_file, out_dir), {"names": names}, "Extracting", done)

    def browse_pack_in(self):
        path = filedialog.askdirectory(title="Folder with the files to pack")
        if path:
            self.pd_pack_in.set(path)
            p = Path(path)
            self._auto(self.pd_pack_out, p.parent / f"{p.name}_new.pd")
            self._update_pack_info()

    def browse_pack_out(self):
        path = self._ask_save("Save .PD file", self.pd_pack_out.get() or "new.pd", ".pd",
                              [("PD files", "*.pd"), ("All", "*.*")])
        if path:
            self.pd_pack_out.set(path)

    def _update_pack_info(self):
        in_dir = self.pd_pack_in.get()
        if self.pack_mode.get() != MODE_AUTO:
            self.pack_info.configure(text="")
        elif in_dir and Path(in_dir).is_dir():
            mode, source = pd_core.resolve_mode(in_dir)
            self.pack_info.configure(text=f"→ {mode} ({source})")
        else:
            self.pack_info.configure(text=f"→ mode from {pd_core.INFO_FILE}, or {pd_core.DEFAULT_MODE}")

    def start_pd_pack(self):
        in_dir, out_file = self.pd_pack_in.get(), self.pd_pack_out.get()
        if not in_dir or not Path(in_dir).is_dir():
            messagebox.showwarning(APP_TITLE, "Choose a valid input folder.")
            return
        if out_file and Path(in_dir).resolve() in Path(out_file).resolve().parents:
            messagebox.showwarning(APP_TITLE, "The output file cannot be inside the input folder.")
            return
        if not self._check_output(out_file):
            return
        mode = None if self.pack_mode.get() == MODE_AUTO else self.pack_mode.get()

        def done(result):
            mode_used, count = result
            self._ask_open(f"Archive created ({mode_used}, {count} files):\n{out_file}", Path(out_file).parent)

        self.run_task(pd_core.pack, (in_dir, out_file), {"mode": mode}, "Packing", done)

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
        style.configure("PD.Treeview", background=bg, fieldbackground=bg, foreground=fg,
                        rowheight=24, borderwidth=0)
        style.map("PD.Treeview", background=[("selected", "#1f6aa5")], foreground=[("selected", "#ffffff")])
        style.configure("PD.Treeview.Heading", background=head_bg, foreground=fg, relief="flat")
        style.map("PD.Treeview.Heading", background=[("active", head_bg)])

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
        self.pd_preview.show_file(name)

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
