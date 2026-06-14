#!/usr/bin/env python3
"""
avrdude GUI
===========

A simple, dependency-free (stdlib only) cross-platform GUI front-end for the
`avrdude` command line tool, including support for avrdude's interactive
"terminal mode" (-t).

Layout strategy for picking the avrdude binary / config file:

  Windows:
    1. Look for avrdude.exe / avrdude.conf next to this script/exe.
    2. Fall back to PATH (shutil.which).
    If neither is found -> error dialog.

  macOS / Linux:
    1. Look for `avrdude` on PATH (shutil.which).
    2. avrdude.conf is expected to be found by avrdude itself (system
       location). If the user wants to override it, they can type a path
       in the "Config file" field.
    If avrdude is not found on PATH -> error dialog.

Programmer / MCU lists:
    Populated by running `avrdude -c ?` and `avrdude -p ?` against the
    located avrdude binary (same approach the official avrdude Python GUI
    uses: ask avrdude itself what it supports). Results are shown in a
    filterable list (type to filter), similar in spirit to the
    grouped/searchable pickers in the official GUI.
"""

import os
import sys
import shutil
import subprocess
import threading
import queue
import platform

import tkinter as tk
from tkinter import ttk, filedialog, messagebox


# --------------------------------------------------------------------------
# Locating avrdude
# --------------------------------------------------------------------------

def app_dir():
    """Directory containing this script (or the frozen exe)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def find_avrdude():
    """
    Returns (avrdude_path, conf_path_or_None, error_message_or_None)
    """
    here = app_dir()
    is_windows = platform.system() == "Windows"
    exe_name = "avrdude.exe" if is_windows else "avrdude"

    if is_windows:
        # 1. next to the exe/script
        local_exe = os.path.join(here, exe_name)
        local_conf = os.path.join(here, "avrdude.conf")
        if os.path.isfile(local_exe):
            conf = local_conf if os.path.isfile(local_conf) else None
            return local_exe, conf, None

        # 2. PATH
        path_exe = shutil.which("avrdude")
        if path_exe:
            # avrdude.conf may sit next to the exe found on PATH
            conf_guess = os.path.join(os.path.dirname(path_exe), "avrdude.conf")
            conf = conf_guess if os.path.isfile(conf_guess) else None
            return path_exe, conf, None

        return None, None, (
            "Could not find avrdude.exe.\n\n"
            "Please place avrdude.exe (and avrdude.conf) in the same folder "
            "as this program, or make sure avrdude is on your PATH."
        )
    else:
        # macOS / Linux - expect avrdude on PATH, conf found by avrdude itself
        path_exe = shutil.which("avrdude")
        if path_exe:
            return path_exe, None, None
        return None, None, (
            "Could not find the 'avrdude' executable on your PATH.\n\n"
            "Please install avrdude (e.g. via Homebrew on macOS or your "
            "distro's package manager on Linux) and make sure it is on "
            "your PATH."
        )


# --------------------------------------------------------------------------
# Helpers to query avrdude for supported programmers / parts
# --------------------------------------------------------------------------

def query_avrdude_list(avrdude_path, conf_path, flag):
    """
    Run `avrdude [-C conf] -c ?` or `-p ?` and parse the resulting list.
    avrdude prints this list to stderr and exits with non-zero status,
    which is normal.

    Returns a list of (id, description) tuples.
    """
    cmd = [avrdude_path]
    if conf_path:
        cmd += ["-C", conf_path]
    cmd += [flag, "?"]

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=15
        )
    except Exception as e:
        return [], str(e)

    output = (proc.stdout or "") + "\n" + (proc.stderr or "")
    items = []
    for line in output.splitlines():
        line = line.rstrip()
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        # Typical lines look like:  "  attiny13  = ATtiny13"
        # or                        "  arduino   = Arduino"
        if "=" not in line:
            continue
        ident, _, desc = line.partition("=")
        ident = ident.strip()
        desc = desc.strip()
        # Skip header-ish lines that slipped through
        if not ident or " " in ident:
            continue
        items.append((ident, desc))
    return items, None


# --------------------------------------------------------------------------
# Filterable picker dialog
# --------------------------------------------------------------------------

class PickerDialog(tk.Toplevel):
    """A simple modal dialog with a search box and a filterable list of
    (id, description) pairs."""

    def __init__(self, parent, title, items):
        super().__init__(parent)
        self.title(title)
        self.result = None
        self.items = items

        self.geometry("520x420")
        self.transient(parent)
        self.grab_set()

        top = ttk.Frame(self, padding=8)
        top.pack(fill="both", expand=True)

        ttk.Label(top, text="Filter:").pack(anchor="w")
        self.filter_var = tk.StringVar()
        entry = ttk.Entry(top, textvariable=self.filter_var)
        entry.pack(fill="x", pady=(0, 6))
        entry.bind("<KeyRelease>", lambda e: self.refresh())
        entry.focus_set()

        cols = ("id", "description")
        self.tree = ttk.Treeview(top, columns=cols, show="headings",
                                  selectmode="browse")
        self.tree.heading("id", text="ID")
        self.tree.heading("description", text="Description")
        self.tree.column("id", width=140, anchor="w")
        self.tree.column("description", width=340, anchor="w")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<Double-1>", lambda e: self.on_ok())

        btns = ttk.Frame(top)
        btns.pack(fill="x", pady=(8, 0))
        ttk.Button(btns, text="OK", command=self.on_ok).pack(side="right")
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="right", padx=6)

        self.refresh()

    def refresh(self):
        f = self.filter_var.get().strip().lower()
        self.tree.delete(*self.tree.get_children())
        for ident, desc in self.items:
            if f and f not in ident.lower() and f not in desc.lower():
                continue
            self.tree.insert("", "end", values=(ident, desc))

    def on_ok(self):
        sel = self.tree.selection()
        if sel:
            self.result = self.tree.item(sel[0], "values")[0]
        self.destroy()


# --------------------------------------------------------------------------
# Main application
# --------------------------------------------------------------------------

class AvrdudeGUI(tk.Tk):

    OPERATIONS = [
        ("Read flash to file",   "flash", "r"),
        ("Write flash from file","flash", "w"),
        ("Verify flash",         "flash", "v"),
        ("Read EEPROM to file",  "eeprom", "r"),
        ("Write EEPROM from file","eeprom", "w"),
        ("Verify EEPROM",        "eeprom", "v"),
    ]

    FORMATS = ["i (Intel Hex)", "r (raw binary)", "s (Motorola S-record)",
               "e (ELF, write only)"]

    def __init__(self):
        super().__init__()
        self.title("avrdude GUI")
        self.geometry("780x640")

        self.avrdude_path = None
        self.conf_path = None
        self.programmers = []
        self.parts = []

        self.proc = None          # currently running subprocess
        self.out_queue = queue.Queue()

        self._build_ui()
        self._locate_avrdude()

        self.after(100, self._poll_output)

    # ---------------------------------------------------------------- UI --

    def _build_ui(self):
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=8, pady=8)

        self.tab_main = ttk.Frame(nb)
        self.tab_terminal = ttk.Frame(nb)
        nb.add(self.tab_main, text="Program")
        nb.add(self.tab_terminal, text="Terminal mode")

        self._build_main_tab(self.tab_main)
        self._build_terminal_tab(self.tab_terminal)

        # Shared output box + buttons live below the notebook
        out_frame = ttk.LabelFrame(self, text="Output")
        out_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        self.output = tk.Text(out_frame, height=14, wrap="word",
                               font=("Consolas" if platform.system() == "Windows"
                                     else "Menlo", 10))
        self.output.pack(fill="both", expand=True, side="top")
        sb = ttk.Scrollbar(self.output, command=self.output.yview)
        self.output.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")

        btn_row = ttk.Frame(out_frame)
        btn_row.pack(fill="x", pady=4)
        ttk.Button(btn_row, text="Copy", command=self.copy_output).pack(side="left", padx=4)
        ttk.Button(btn_row, text="Clear", command=self.clear_output).pack(side="left", padx=4)
        self.status_var = tk.StringVar(value="")
        ttk.Label(btn_row, textvariable=self.status_var).pack(side="left", padx=12)

        self.btn_cancel = ttk.Button(btn_row, text="Cancel", command=self.cancel_run, state="disabled")
        self.btn_cancel.pack(side="right", padx=4)

    def _build_main_tab(self, parent):
        frm = ttk.Frame(parent, padding=8)
        frm.pack(fill="both", expand=True)

        # Programmer / Part / Port row
        row = 0
        ttk.Label(frm, text="Programmer (-c):").grid(row=row, column=0, sticky="w")
        self.programmer_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.programmer_var, width=20).grid(row=row, column=1, sticky="w")
        ttk.Button(frm, text="Select...", command=self.pick_programmer).grid(row=row, column=2, padx=4)

        ttk.Label(frm, text="MCU (-p):").grid(row=row, column=3, sticky="w", padx=(16, 0))
        self.part_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.part_var, width=14).grid(row=row, column=4, sticky="w")
        ttk.Button(frm, text="Select...", command=self.pick_part).grid(row=row, column=5, padx=4)

        row += 1
        ttk.Label(frm, text="Port (-P):").grid(row=row, column=0, sticky="w", pady=(6, 0))
        self.port_var = tk.StringVar(value="usb")
        port_entry = ttk.Combobox(frm, textvariable=self.port_var, width=18,
                                   values=self._guess_ports())
        port_entry.grid(row=row, column=1, sticky="w", pady=(6, 0))

        ttk.Label(frm, text="Baud (-b):").grid(row=row, column=3, sticky="w", padx=(16, 0), pady=(6, 0))
        self.baud_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.baud_var, width=10).grid(row=row, column=4, sticky="w", pady=(6, 0))

        row += 1
        ttk.Label(frm, text="Config file (-C, optional):").grid(row=row, column=0, sticky="w", pady=(6, 0))
        self.conf_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.conf_var, width=40).grid(row=row, column=1, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Button(frm, text="Browse...", command=self.browse_conf).grid(row=row, column=4, sticky="w", pady=(6, 0))

        row += 1
        ttk.Separator(frm).grid(row=row, column=0, columnspan=6, sticky="ew", pady=10)

        # Operation block
        row += 1
        ttk.Label(frm, text="Operation:").grid(row=row, column=0, sticky="w")
        self.op_var = tk.StringVar(value=self.OPERATIONS[0][0])
        op_combo = ttk.Combobox(frm, textvariable=self.op_var, width=22,
                                 values=[o[0] for o in self.OPERATIONS], state="readonly")
        op_combo.grid(row=row, column=1, columnspan=2, sticky="w")

        ttk.Label(frm, text="Format (-i/o):").grid(row=row, column=3, sticky="w", padx=(16, 0))
        self.format_var = tk.StringVar(value=self.FORMATS[0])
        ttk.Combobox(frm, textvariable=self.format_var, width=18,
                     values=self.FORMATS, state="readonly").grid(row=row, column=4, sticky="w")

        row += 1
        ttk.Label(frm, text="File:").grid(row=row, column=0, sticky="w", pady=(6, 0))
        self.file_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.file_var, width=40).grid(row=row, column=1, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Button(frm, text="Browse...", command=self.browse_file).grid(row=row, column=4, sticky="w", pady=(6, 0))

        row += 1
        ttk.Separator(frm).grid(row=row, column=0, columnspan=6, sticky="ew", pady=10)

        # Extra options
        row += 1
        self.disable_verify_var = tk.BooleanVar()
        ttk.Checkbutton(frm, text="Disable auto-verify (-V)", variable=self.disable_verify_var).grid(row=row, column=0, columnspan=2, sticky="w")
        self.disable_erase_var = tk.BooleanVar()
        ttk.Checkbutton(frm, text="Disable chip erase (-D)", variable=self.disable_erase_var).grid(row=row, column=2, columnspan=2, sticky="w")

        row += 1
        ttk.Label(frm, text="Extra args:").grid(row=row, column=0, sticky="w", pady=(6, 0))
        self.extra_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.extra_var, width=60).grid(row=row, column=1, columnspan=4, sticky="w", pady=(6, 0))

        row += 1
        ttk.Separator(frm).grid(row=row, column=0, columnspan=6, sticky="ew", pady=10)

        # Command preview + run
        row += 1
        ttk.Label(frm, text="Command:").grid(row=row, column=0, sticky="w")
        self.cmd_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.cmd_var, state="readonly").grid(row=row, column=1, columnspan=5, sticky="ew")

        row += 1
        btns = ttk.Frame(frm)
        btns.grid(row=row, column=0, columnspan=6, sticky="w", pady=8)
        ttk.Button(btns, text="Update command preview", command=self.update_command_preview).pack(side="left", padx=4)
        ttk.Button(btns, text="Run", command=lambda: self.run_avrdude()).pack(side="left", padx=4)

        for c in range(6):
            frm.columnconfigure(c, weight=1)

        # update preview on any change
        for v in (self.programmer_var, self.part_var, self.port_var, self.baud_var,
                  self.conf_var, self.op_var, self.format_var, self.file_var,
                  self.disable_verify_var, self.disable_erase_var, self.extra_var):
            v.trace_add("write", lambda *a: self.update_command_preview())

    def _build_terminal_tab(self, parent):
        frm = ttk.Frame(parent, padding=8)
        frm.pack(fill="both", expand=True)

        info = ("Terminal mode runs `avrdude -t ... ` and sends the commands "
                "below to its interactive prompt (one per line). "
                "Common commands: d/dump, w/write, p/part, sig, q/quit.")
        ttk.Label(frm, text=info, wraplength=720, justify="left").pack(anchor="w", pady=(0, 6))

        ttk.Label(frm, text="Commands (one per line, 'quit' is appended automatically):").pack(anchor="w")
        self.term_cmds = tk.Text(frm, height=8)
        self.term_cmds.pack(fill="both", expand=False, pady=(0, 8))
        self.term_cmds.insert("1.0", "sig\n")

        ttk.Button(frm, text="Run terminal session", command=self.run_terminal).pack(anchor="w")

    # --------------------------------------------------------------- init --

    def _guess_ports(self):
        sysname = platform.system()
        ports = []
        if sysname == "Windows":
            ports = [f"COM{i}" for i in range(1, 11)]
        elif sysname == "Darwin":
            try:
                ports = [os.path.join("/dev", p) for p in os.listdir("/dev")
                         if p.startswith("cu.") or p.startswith("tty.")]
            except OSError:
                pass
        else:
            try:
                ports = [os.path.join("/dev", p) for p in os.listdir("/dev")
                         if p.startswith("ttyUSB") or p.startswith("ttyACM")]
            except OSError:
                pass
        ports.append("usb")
        return ports

    def _locate_avrdude(self):
        path, conf, err = find_avrdude()
        if err:
            messagebox.showerror("avrdude not found", err)
            self.status_var.set("avrdude not found")
            return
        self.avrdude_path = path
        self.conf_path = conf
        if conf:
            self.conf_var.set(conf)
        self.status_var.set(f"avrdude: {path}")

        # populate programmer / part lists in background
        threading.Thread(target=self._load_lists, daemon=True).start()

    def _load_lists(self):
        progs, perr = query_avrdude_list(self.avrdude_path, self.conf_path_value(), "-c")
        parts, parr = query_avrdude_list(self.avrdude_path, self.conf_path_value(), "-p")
        self.programmers = sorted(progs)
        self.parts = sorted(parts)

    def conf_path_value(self):
        v = self.conf_var.get().strip()
        return v if v else self.conf_path

    # ------------------------------------------------------------- pickers --

    def pick_programmer(self):
        if not self.programmers:
            messagebox.showinfo("Please wait", "Programmer list is still loading, or avrdude could not be queried.")
            return
        dlg = PickerDialog(self, "Select programmer", self.programmers)
        self.wait_window(dlg)
        if dlg.result:
            self.programmer_var.set(dlg.result)

    def pick_part(self):
        if not self.parts:
            messagebox.showinfo("Please wait", "Part list is still loading, or avrdude could not be queried.")
            return
        dlg = PickerDialog(self, "Select MCU", self.parts)
        self.wait_window(dlg)
        if dlg.result:
            self.part_var.set(dlg.result)

    def browse_file(self):
        f = filedialog.askopenfilename() if "Write" in self.op_var.get() else filedialog.asksaveasfilename()
        if f:
            self.file_var.set(f)

    def browse_conf(self):
        f = filedialog.askopenfilename(filetypes=[("avrdude conf", "avrdude.conf"), ("All files", "*.*")])
        if f:
            self.conf_var.set(f)

    # ---------------------------------------------------------- command ----

    def build_args(self):
        if not self.avrdude_path:
            raise RuntimeError("avrdude was not found. See the error shown at startup.")

        args = [self.avrdude_path]

        conf = self.conf_var.get().strip()
        if conf:
            args += ["-C", conf]

        prog = self.programmer_var.get().strip()
        if prog:
            args += ["-c", prog]

        part = self.part_var.get().strip()
        if part:
            args += ["-p", part]

        port = self.port_var.get().strip()
        if port:
            args += ["-P", port]

        baud = self.baud_var.get().strip()
        if baud:
            args += ["-b", baud]

        if self.disable_verify_var.get():
            args.append("-V")
        if self.disable_erase_var.get():
            args.append("-D")

        # Operation -> -U memtype:op:filename:format
        op_label = self.op_var.get()
        memtype, op = None, None
        for label, mem, o in self.OPERATIONS:
            if label == op_label:
                memtype, op = mem, o
                break

        fmt_full = self.format_var.get()
        fmt = fmt_full.split()[0] if fmt_full else "i"

        fname = self.file_var.get().strip()
        if memtype and op:
            if op in ("r", "w") and not fname:
                raise RuntimeError("Please choose a file for this operation.")
            target = fname if fname else "-"
            args += ["-U", f"{memtype}:{op}:{target}:{fmt}"]

        extra = self.extra_var.get().strip()
        if extra:
            args += extra.split()

        return args

    def update_command_preview(self):
        try:
            args = self.build_args()
            self.cmd_var.set(" ".join(self._quote(a) for a in args))
        except Exception as e:
            self.cmd_var.set(f"<{e}>")

    @staticmethod
    def _quote(s):
        if " " in s or "" == s:
            return f'"{s}"'
        return s

    # ------------------------------------------------------------- running --

    def run_avrdude(self, stdin_text=None):
        if self.proc is not None:
            messagebox.showwarning("Busy", "A command is already running.")
            return
        try:
            args = self.build_args()
        except Exception as e:
            messagebox.showerror("Error", str(e))
            return

        self.update_command_preview()
        self._append(f"$ {' '.join(args)}\n")
        self.status_var.set("Running...")
        self.btn_cancel.configure(state="normal")

        def worker():
            try:
                self.proc = subprocess.Popen(
                    args,
                    stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
            except Exception as e:
                self.out_queue.put(("line", f"Failed to start avrdude: {e}\n"))
                self.out_queue.put(("done", None))
                return

            if stdin_text is not None:
                try:
                    self.proc.stdin.write(stdin_text)
                    self.proc.stdin.close()
                except Exception:
                    pass

            for line in self.proc.stdout:
                self.out_queue.put(("line", line))

            self.proc.wait()
            self.out_queue.put(("line", f"\n[process exited with code {self.proc.returncode}]\n"))
            self.out_queue.put(("done", None))

        threading.Thread(target=worker, daemon=True).start()

    def run_terminal(self):
        cmds = self.term_cmds.get("1.0", "end").strip().splitlines()
        cmds = [c for c in cmds if c.strip()]
        if not any(c.strip().lower() in ("q", "quit") for c in cmds):
            cmds.append("quit")
        stdin_text = "\n".join(cmds) + "\n"

        # Add -t to a copy of the args
        try:
            args = self.build_args()
        except Exception as e:
            messagebox.showerror("Error", str(e))
            return
        if "-t" not in args:
            args.append("-t")

        if self.proc is not None:
            messagebox.showwarning("Busy", "A command is already running.")
            return

        self._append(f"$ {' '.join(args)}\n")
        for c in cmds:
            self._append(f">>> {c}\n")
        self.status_var.set("Running terminal session...")
        self.btn_cancel.configure(state="normal")

        def worker():
            try:
                self.proc = subprocess.Popen(
                    args,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
            except Exception as e:
                self.out_queue.put(("line", f"Failed to start avrdude: {e}\n"))
                self.out_queue.put(("done", None))
                return

            try:
                self.proc.stdin.write(stdin_text)
                self.proc.stdin.close()
            except Exception:
                pass

            for line in self.proc.stdout:
                self.out_queue.put(("line", line))

            self.proc.wait()
            self.out_queue.put(("line", f"\n[process exited with code {self.proc.returncode}]\n"))
            self.out_queue.put(("done", None))

        threading.Thread(target=worker, daemon=True).start()

    def cancel_run(self):
        if self.proc is not None:
            try:
                self.proc.terminate()
            except Exception:
                pass

    def _poll_output(self):
        try:
            while True:
                kind, payload = self.out_queue.get_nowait()
                if kind == "line":
                    self._append(payload)
                elif kind == "done":
                    self.proc = None
                    self.status_var.set("Idle")
                    self.btn_cancel.configure(state="disabled")
        except queue.Empty:
            pass
        self.after(100, self._poll_output)

    # ------------------------------------------------------------- output --

    def _append(self, text):
        self.output.insert("end", text)
        self.output.see("end")

    def copy_output(self):
        self.clipboard_clear()
        self.clipboard_append(self.output.get("1.0", "end"))

    def clear_output(self):
        self.output.delete("1.0", "end")


if __name__ == "__main__":
    app = AvrdudeGUI()
    app.mainloop()
