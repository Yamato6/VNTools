#!/usr/bin/env python3
"""
CrossChannel MultiTool GUI - Interfaz gráfica para archivos .PD (FS Engine)
Juego: CROSS†CHANNEL
"""

import os
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk

import pd_core

APP_TITLE = "CrossChannel MultiTool"
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


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("820x600")
        self.minsize(640, 480)

        self.events = queue.Queue()
        self.cancel_event = threading.Event()
        self.worker = None
        self.busy_widgets = []

        # Estado de la pestaña Extraer
        self.entries = []           # lista de pd_core.PDEntry
        self.pd_mode = None

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
        self._build_pack_tab(self.tabs.add("Pack"))
        self._build_status_bar()

    def _build_extract_tab(self, tab):
        tab.grid_columnconfigure(1, weight=1)
        tab.grid_rowconfigure(3, weight=1)

        self.pd_var = tk.StringVar()
        ctk.CTkLabel(tab, text=".PD file:").grid(row=0, column=0, sticky="w", padx=(4, 8), pady=4)
        pd_entry = ctk.CTkEntry(tab, textvariable=self.pd_var)
        pd_entry.grid(row=0, column=1, sticky="ew", pady=4)
        pd_entry.bind("<Return>", lambda _e: self.load_pd(self.pd_var.get()))
        btn = ctk.CTkButton(tab, text="Open…", width=100, command=self.browse_pd)
        btn.grid(row=0, column=2, padx=(8, 4), pady=4)
        self.busy_widgets.append(btn)

        self.pd_info = ctk.CTkLabel(tab, text="No file open", anchor="w",
                                    text_color=("gray35", "gray65"))
        self.pd_info.grid(row=1, column=0, columnspan=3, sticky="ew", padx=4)

        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *_: self._refresh_tree())
        ctk.CTkLabel(tab, text="Filter:").grid(row=2, column=0, sticky="w", padx=(4, 8), pady=4)
        ctk.CTkEntry(tab, textvariable=self.filter_var, placeholder_text="name contains…") \
            .grid(row=2, column=1, columnspan=2, sticky="ew", padx=(0, 4), pady=4)

        tree_frame = ctk.CTkFrame(tab, fg_color="transparent")
        tree_frame.grid(row=3, column=0, columnspan=3, sticky="nsew", padx=4, pady=4)
        tree_frame.grid_columnconfigure(0, weight=1)
        tree_frame.grid_rowconfigure(0, weight=1)

        self.tree = ttk.Treeview(tree_frame, columns=("size",), selectmode="extended",
                                 style="PD.Treeview")
        self.tree.heading("#0", text="Name", anchor="w")
        self.tree.heading("size", text="Size", anchor="e")
        self.tree.column("#0", stretch=True, width=500)
        self.tree.column("size", stretch=False, width=110, anchor="e")
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ctk.CTkScrollbar(tree_frame, command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._update_selection_label())

        self.out_var = tk.StringVar()
        ctk.CTkLabel(tab, text="Output:").grid(row=4, column=0, sticky="w", padx=(4, 8), pady=4)
        ctk.CTkEntry(tab, textvariable=self.out_var).grid(row=4, column=1, sticky="ew", pady=4)
        btn = ctk.CTkButton(tab, text="Browse…", width=100, command=self.browse_out_dir)
        btn.grid(row=4, column=2, padx=(8, 4), pady=4)
        self.busy_widgets.append(btn)

        actions = ctk.CTkFrame(tab, fg_color="transparent")
        actions.grid(row=5, column=0, columnspan=3, sticky="ew", padx=4, pady=(4, 0))
        self.sel_label = ctk.CTkLabel(actions, text="", text_color=("gray35", "gray65"))
        self.sel_label.pack(side="left")
        btn_all = ctk.CTkButton(actions, text="Extract all", command=lambda: self.start_extract(False))
        btn_all.pack(side="right")
        btn_sel = ctk.CTkButton(actions, text="Extract selected",
                                command=lambda: self.start_extract(True))
        btn_sel.pack(side="right", padx=8)
        self.busy_widgets += [btn_all, btn_sel]

    def _build_pack_tab(self, tab):
        tab.grid_columnconfigure(1, weight=1)

        self.in_var = tk.StringVar()
        ctk.CTkLabel(tab, text="Input folder:").grid(row=0, column=0, sticky="w", padx=(4, 8), pady=6)
        in_entry = ctk.CTkEntry(tab, textvariable=self.in_var)
        in_entry.grid(row=0, column=1, sticky="ew", pady=6)
        in_entry.bind("<Return>", lambda _e: self._update_pack_info())
        in_entry.bind("<FocusOut>", lambda _e: self._update_pack_info())
        btn = ctk.CTkButton(tab, text="Browse…", width=100, command=self.browse_in_dir)
        btn.grid(row=0, column=2, padx=(8, 4), pady=6)
        self.busy_widgets.append(btn)

        self.in_info = ctk.CTkLabel(tab, text="", anchor="w", text_color=("gray35", "gray65"))
        self.in_info.grid(row=1, column=1, columnspan=2, sticky="ew")

        self.pack_out_var = tk.StringVar()
        ctk.CTkLabel(tab, text="Output .PD file:").grid(row=2, column=0, sticky="w", padx=(4, 8), pady=6)
        ctk.CTkEntry(tab, textvariable=self.pack_out_var).grid(row=2, column=1, sticky="ew", pady=6)
        btn = ctk.CTkButton(tab, text="Save as…", width=100, command=self.browse_pack_out)
        btn.grid(row=2, column=2, padx=(8, 4), pady=6)
        self.busy_widgets.append(btn)

        ctk.CTkLabel(tab, text="Mode:").grid(row=3, column=0, sticky="w", padx=(4, 8), pady=6)
        self.mode_var = tk.StringVar(value=MODE_AUTO)
        mode_btn = ctk.CTkSegmentedButton(tab, values=[MODE_AUTO, "PackOnly", "PackPlus"],
                                          variable=self.mode_var,
                                          command=lambda _v: self._update_pack_info())
        mode_btn.grid(row=3, column=1, sticky="w", pady=6)
        self.busy_widgets.append(mode_btn)

        self.mode_info = ctk.CTkLabel(tab, text="", anchor="w", text_color=("gray35", "gray65"))
        self.mode_info.grid(row=4, column=1, columnspan=2, sticky="ew")

        ctk.CTkLabel(tab, text="PackPlus: data inverted with XOR 0xFF (used by most of the "
                               "game files).\nPackOnly: unencrypted data.",
                     justify="left", anchor="w", text_color=("gray35", "gray65")) \
            .grid(row=5, column=1, columnspan=2, sticky="ew", pady=(12, 0))

        btn = ctk.CTkButton(tab, text="Pack", width=160, command=self.start_pack)
        btn.grid(row=6, column=1, columnspan=2, sticky="e", padx=4, pady=(24, 0))
        self.busy_widgets.append(btn)

        self._update_pack_info()

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
        sel = "#1f6aa5"
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("PD.Treeview", background=bg, fieldbackground=bg, foreground=fg,
                        rowheight=24, borderwidth=0)
        style.map("PD.Treeview", background=[("selected", sel)], foreground=[("selected", "#ffffff")])
        style.configure("PD.Treeview.Heading", background=head_bg, foreground=fg, relief="flat")
        style.map("PD.Treeview.Heading", background=[("active", head_bg)])

    # ------------------------------------------------------- Pestaña Extraer

    def browse_pd(self):
        path = filedialog.askopenfilename(title="Open .PD file",
                                          filetypes=[("PD files", "*.pd"), ("All", "*.*")])
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
        self.pd_var.set(path)
        self.entries = entries
        self.pd_mode = mode
        p = Path(path)
        self.out_var.set(str(p.parent / p.stem))

        total = sum(e.size for e in entries)
        info = f"Mode: {mode}   ·   {len(entries)} files   ·   {format_size(total)}"
        if warnings:
            info += "   ·   " + "; ".join(warnings)
        self.pd_info.configure(text=info)
        self._refresh_tree()

    def _refresh_tree(self):
        self.tree.delete(*self.tree.get_children())
        text = self.filter_var.get().lower()
        for i, e in enumerate(self.entries):
            if text in e.name.lower():
                self.tree.insert("", "end", iid=str(i), text=e.name, values=(format_size(e.size),))
        self._update_selection_label()

    def _update_selection_label(self):
        if not self.entries:
            self.sel_label.configure(text="")
            return
        shown = len(self.tree.get_children())
        selected = len(self.tree.selection())
        self.sel_label.configure(text=f"{shown} shown · {selected} selected")

    def browse_out_dir(self):
        path = filedialog.askdirectory(title="Output folder")
        if path:
            self.out_var.set(path)

    def start_extract(self, only_selected):
        pd_file = self.pd_var.get().strip()
        out_dir = self.out_var.get().strip()
        if not self.entries:
            messagebox.showwarning(APP_TITLE, "Open a .PD file first.")
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

        def done(result):
            mode, count = result
            if messagebox.askyesno(APP_TITLE, f"Extracted {count} files to:\n{out_dir}\n\n"
                                              "Open the folder?"):
                open_folder(out_dir)

        self.run_task(pd_core.extract, (pd_file, out_dir),
                      {"names": names}, "Extracting", done)

    # ---------------------------------------------------- Pestaña Empaquetar

    def browse_in_dir(self):
        path = filedialog.askdirectory(title="Folder with the files to pack")
        if path:
            self.in_var.set(path)
            if not self.pack_out_var.get():
                p = Path(path)
                self.pack_out_var.set(str(p.parent / f"{p.name}_new.pd"))
            self._update_pack_info()

    def browse_pack_out(self):
        initial = Path(self.pack_out_var.get() or "new.pd")
        path = filedialog.asksaveasfilename(title="Save .PD file", defaultextension=".pd",
                                            initialdir=str(initial.parent) if initial.parent.exists() else None,
                                            initialfile=initial.name,
                                            filetypes=[("PD files", "*.pd"), ("All", "*.*")])
        if path:
            self.pack_out_var.set(path)

    def _update_pack_info(self):
        in_dir = self.in_var.get().strip()
        if in_dir and Path(in_dir).is_dir():
            count = len(pd_core.collect_files(in_dir))
            has_info = pd_core.read_info(in_dir) is not None
            text = f"{count} files"
            text += f"  ·  {pd_core.INFO_FILE} found (the original order is kept)" if has_info \
                else f"  ·  no {pd_core.INFO_FILE} (alphabetical order)"
            self.in_info.configure(text=text)
        else:
            self.in_info.configure(text="")

        if self.mode_var.get() == MODE_AUTO:
            if in_dir and Path(in_dir).is_dir():
                mode, source = pd_core.resolve_mode(in_dir)
                self.mode_info.configure(text=f"Auto → {mode} will be used ({source})")
            else:
                self.mode_info.configure(text=f"Auto → mode from {pd_core.INFO_FILE}, or "
                                              f"{pd_core.DEFAULT_MODE} by default")
        else:
            self.mode_info.configure(text="")

    def start_pack(self):
        in_dir = self.in_var.get().strip()
        out_file = self.pack_out_var.get().strip()
        if not in_dir or not Path(in_dir).is_dir():
            messagebox.showwarning(APP_TITLE, "Choose a valid input folder.")
            return
        if not out_file:
            messagebox.showwarning(APP_TITLE, "Choose the output .PD file.")
            return
        in_path, out_path = Path(in_dir).resolve(), Path(out_file).resolve()
        if in_path in out_path.parents:
            messagebox.showwarning(APP_TITLE, "The output file cannot be inside "
                                              "the input folder.")
            return
        if out_path.exists() and not messagebox.askyesno(
                APP_TITLE, f"{out_path.name} already exists.\nOverwrite it?"):
            return

        mode = None if self.mode_var.get() == MODE_AUTO else self.mode_var.get()

        def done(result):
            mode_used, count = result
            if messagebox.askyesno(APP_TITLE, f"Archive created ({mode_used}, {count} files):\n"
                                              f"{out_path}\n\nOpen the folder?"):
                open_folder(out_path.parent)

        self.run_task(pd_core.pack, (in_dir, out_file), {"mode": mode}, "Packing", done)

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
            except pd_core.Cancelled:
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
                        self.status.configure(text=f"{self._task_label} [{i + 1}/{total}]  {name}")
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
        self._update_pack_info()

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
