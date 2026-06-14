#!/usr/bin/env python3
"""
avrdude GUI (tkinter version)
==============================

A single-file, stdlib-only cross-platform GUI for avrdude.

Operation modes on the main tab:
  • Flash / EEPROM read, write, verify  (-U memtype:op:file:fmt)
  • Connect / detect chip               (no -U, just connects and exits)
  • Terminal mode                        (-t, commands typed inline)

Programmer / MCU pickers have dual ID + Description filter fields.
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
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def find_avrdude():
    """Returns (avrdude_path, conf_path_or_None, error_message_or_None)."""
    here = app_dir()
    is_windows = platform.system() == "Windows"
    exe_name = "avrdude.exe" if is_windows else "avrdude"

    if is_windows:
        local_exe = os.path.join(here, exe_name)
        local_conf = os.path.join(here, "avrdude.conf")
        if os.path.isfile(local_exe):
            conf = local_conf if os.path.isfile(local_conf) else None
            return local_exe, conf, None

        path_exe = shutil.which("avrdude")
        if path_exe:
            conf_guess = os.path.join(os.path.dirname(path_exe), "avrdude.conf")
            conf = conf_guess if os.path.isfile(conf_guess) else None
            return path_exe, conf, None

        return None, None, (
            "Could not find avrdude.exe.\n\n"
            "Please place avrdude.exe (and avrdude.conf) in the same folder "
            "as this program, or make sure avrdude is on your PATH."
        )
    else:
        path_exe = shutil.which("avrdude")
        if path_exe:
            return path_exe, None, None
        return None, None, (
            "Could not find the 'avrdude' executable on your PATH.\n\n"
            "Please install avrdude (e.g. via Homebrew on macOS or your "
            "distro's package manager on Linux) and make sure it is on "
            "your PATH."
        )


def query_avrdude_list(avrdude_path, conf_path, flag):
    """Run `avrdude [-C conf] -c ?` / `-p ?` and return list of (id, desc)."""
    cmd = [avrdude_path]
    if conf_path:
        cmd += ["-C", conf_path]
    cmd += [flag, "?"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except Exception as e:
        return [], str(e)
    output = (proc.stdout or "") + "\n" + (proc.stderr or "")
    items = []
    for line in output.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in line:
            continue
        ident, _, desc = line.partition("=")
        ident, desc = ident.strip(), desc.strip()
        if not ident or " " in ident:
            continue
        items.append((ident, desc))
    return items, None


# --------------------------------------------------------------------------
# Operation catalogue
# --------------------------------------------------------------------------

# Special sentinel labels (no -U flag)
OP_CONNECT  = "Connect / detect chip"
OP_TERMINAL = "Terminal mode (-t)"

# Normal memory operations: (label, memtype, op_char)
MEM_OPERATIONS = [
    ("Read flash to file",      "flash",  "r"),
    ("Write flash from file",   "flash",  "w"),
    ("Verify flash",            "flash",  "v"),
    ("Read EEPROM to file",     "eeprom", "r"),
    ("Write EEPROM from file",  "eeprom", "w"),
    ("Verify EEPROM",           "eeprom", "v"),
]

ALL_OP_LABELS = (
    [OP_CONNECT, OP_TERMINAL]
    + [o[0] for o in MEM_OPERATIONS]
)

FORMATS = [
    "i (Intel Hex)",
    "r (raw binary)",
    "s (Motorola S-record)",
    "e (ELF, write only)",
]

TERMINAL_HINT = (
    "Enter avrdude terminal commands below, one per line.\n"
    "'quit' is appended automatically.\n"
    "Examples:  sig   dump flash 0 64   write eeprom 0 0xff   part"
)


# --------------------------------------------------------------------------
# Filterable picker dialog  (dual ID + Description filters)
# --------------------------------------------------------------------------

class PickerDialog(tk.Toplevel):
    """
    Modal picker with two independent filter fields:
      • Filter ID          – matches the short identifier
      • Filter description – matches the long description
    Both filters applied simultaneously (AND logic).
    """

    def __init__(self, parent, title, items):
        super().__init__(parent)
        self.title(title)
        self.result = None
        self.items = items

        self.geometry("600x460")
        self.minsize(480, 340)
        self.transient(parent)
        self.grab_set()

        top = ttk.Frame(self, padding=8)
        top.pack(fill="both", expand=True)

        # dual filter row
        ff = ttk.Frame(top)
        ff.pack(fill="x", pady=(0, 4))

        ttk.Label(ff, text="Filter ID:").pack(side="left")
        self.filter_id_var = tk.StringVar()
        id_entry = ttk.Entry(ff, textvariable=self.filter_id_var, width=18)
        id_entry.pack(side="left", padx=(2, 10))
        id_entry.bind("<KeyRelease>", lambda e: self._refresh())

        ttk.Label(ff, text="Filter description:").pack(side="left")
        self.filter_desc_var = tk.StringVar()
        desc_entry = ttk.Entry(ff, textvariable=self.filter_desc_var, width=28)
        desc_entry.pack(side="left", padx=(2, 10))
        desc_entry.bind("<KeyRelease>", lambda e: self._refresh())

        ttk.Button(ff, text="Clear", width=6,
                   command=self._clear_filters).pack(side="left")

        # count label
        self.count_var = tk.StringVar()
        ttk.Label(top, textvariable=self.count_var,
                  foreground="gray").pack(anchor="w")

        # tree + scrollbar
        tf = ttk.Frame(top)
        tf.pack(fill="both", expand=True, pady=(2, 0))

        self.tree = ttk.Treeview(tf, columns=("id", "description"),
                                  show="headings", selectmode="browse")
        self.tree.heading("id", text="ID",
                          command=lambda: self._sort_col("id", False))
        self.tree.heading("description", text="Description",
                          command=lambda: self._sort_col("description", False))
        self.tree.column("id", width=160, anchor="w")
        self.tree.column("description", width=380, anchor="w")

        vsb = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        self.tree.bind("<Double-1>", lambda e: self.on_ok())
        self.tree.bind("<Return>",   lambda e: self.on_ok())

        # buttons
        btns = ttk.Frame(top)
        btns.pack(fill="x", pady=(8, 0))
        ttk.Button(btns, text="OK",     command=self.on_ok).pack(side="right")
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="right", padx=6)

        self._refresh()
        id_entry.focus_set()

    def _clear_filters(self):
        self.filter_id_var.set("")
        self.filter_desc_var.set("")
        self._refresh()

    def _refresh(self):
        fi = self.filter_id_var.get().strip().lower()
        fd = self.filter_desc_var.get().strip().lower()
        self.tree.delete(*self.tree.get_children())
        count = 0
        for ident, desc in self.items:
            if fi and fi not in ident.lower():
                continue
            if fd and fd not in desc.lower():
                continue
            self.tree.insert("", "end", values=(ident, desc))
            count += 1
        total = len(self.items)
        self.count_var.set(
            f"{count} of {total} entries shown" if (fi or fd) else f"{total} entries"
        )

    def _sort_col(self, col, reverse):
        data = [(self.tree.set(k, col), k) for k in self.tree.get_children("")]
        data.sort(reverse=reverse)
        for i, (_, k) in enumerate(data):
            self.tree.move(k, "", i)
        self.tree.heading(col, command=lambda: self._sort_col(col, not reverse))

    def on_ok(self):
        sel = self.tree.selection()
        if sel:
            self.result = self.tree.item(sel[0], "values")[0]
        self.destroy()


# --------------------------------------------------------------------------
# Main application
# --------------------------------------------------------------------------

class AvrdudeGUI(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("avrdude GUI")
        self.geometry("820x700")
        self.minsize(700, 580)

        self.avrdude_path = None
        self.conf_path    = None
        self.programmers  = []
        self.parts        = []

        self.proc      = None
        self.out_queue = queue.Queue()

        self._build_ui()
        self._locate_avrdude()
        self.after(100, self._poll_output)

    # ------------------------------------------------------------------ UI

    def _build_ui(self):
        # ---- single main frame (no notebook needed any more) ----
        main_outer = ttk.Frame(self)
        main_outer.pack(fill="both", expand=False, padx=8, pady=(8, 4))
        self._build_main_tab(main_outer)

        # ---- output ----
        out_frame = ttk.LabelFrame(self, text="Output")
        out_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        self.output = tk.Text(
            out_frame, height=14, wrap="word",
            font=("Consolas" if platform.system() == "Windows" else "Menlo", 10)
        )
        self.output.pack(fill="both", expand=True, side="top")
        sb = ttk.Scrollbar(self.output, command=self.output.yview)
        self.output.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")

        btn_row = ttk.Frame(out_frame)
        btn_row.pack(fill="x", pady=4)
        ttk.Button(btn_row, text="Copy",  command=self.copy_output).pack(side="left", padx=4)
        ttk.Button(btn_row, text="Clear", command=self.clear_output).pack(side="left", padx=4)
        self.status_var = tk.StringVar(value="")
        ttk.Label(btn_row, textvariable=self.status_var).pack(side="left", padx=12)
        self.btn_cancel = ttk.Button(btn_row, text="Cancel",
                                      command=self.cancel_run, state="disabled")
        self.btn_cancel.pack(side="right", padx=4)

    def _build_main_tab(self, parent):
        frm = ttk.Frame(parent, padding=4)
        frm.pack(fill="both", expand=True)

        # ---- row 0: programmer / part ----
        r = 0
        ttk.Label(frm, text="Programmer (-c):").grid(row=r, column=0, sticky="e", padx=(0, 4))
        self.programmer_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.programmer_var, width=20).grid(row=r, column=1, sticky="w")
        ttk.Button(frm, text="Select…", command=self.pick_programmer).grid(row=r, column=2, padx=4)

        ttk.Label(frm, text="MCU (-p):").grid(row=r, column=3, sticky="e", padx=(12, 4))
        self.part_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.part_var, width=14).grid(row=r, column=4, sticky="w")
        ttk.Button(frm, text="Select…", command=self.pick_part).grid(row=r, column=5, padx=4)

        # ---- row 1: port / baud ----
        r += 1
        ttk.Label(frm, text="Port (-P):").grid(row=r, column=0, sticky="e", padx=(0, 4), pady=(6, 0))
        self.port_var = tk.StringVar(value="usb")
        ttk.Combobox(frm, textvariable=self.port_var, width=18,
                     values=self._guess_ports()).grid(row=r, column=1, sticky="w", pady=(6, 0))

        ttk.Label(frm, text="Baud (-b):").grid(row=r, column=3, sticky="e", padx=(12, 4), pady=(6, 0))
        self.baud_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.baud_var, width=10).grid(row=r, column=4, sticky="w", pady=(6, 0))

        # ---- row 2: config file ----
        r += 1
        ttk.Label(frm, text="Config file (-C):").grid(row=r, column=0, sticky="e", padx=(0, 4), pady=(6, 0))
        self.conf_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.conf_var, width=40).grid(
            row=r, column=1, columnspan=3, sticky="ew", pady=(6, 0))
        ttk.Button(frm, text="Browse…", command=self.browse_conf).grid(row=r, column=4, pady=(6, 0))

        # ---- separator ----
        r += 1
        ttk.Separator(frm).grid(row=r, column=0, columnspan=6, sticky="ew", pady=8)

        # ---- row: operation + format ----
        r += 1
        ttk.Label(frm, text="Operation:").grid(row=r, column=0, sticky="e", padx=(0, 4))
        self.op_var = tk.StringVar(value=OP_CONNECT)
        self.op_combo = ttk.Combobox(frm, textvariable=self.op_var,
                                      values=ALL_OP_LABELS,
                                      state="readonly", width=26)
        self.op_combo.grid(row=r, column=1, columnspan=2, sticky="w")
        self.op_var.trace_add("write", lambda *_: self._on_op_change())

        ttk.Label(frm, text="Format:").grid(row=r, column=3, sticky="e", padx=(12, 4))
        self.format_var = tk.StringVar(value=FORMATS[0])
        self.format_combo = ttk.Combobox(frm, textvariable=self.format_var,
                                          values=FORMATS, state="readonly", width=20)
        self.format_combo.grid(row=r, column=4, sticky="w")

        # ---- row: file (hidden for Connect / Terminal) ----
        r += 1
        self.file_label = ttk.Label(frm, text="File:")
        self.file_label.grid(row=r, column=0, sticky="e", padx=(0, 4), pady=(6, 0))
        self.file_var = tk.StringVar()
        self.file_entry = ttk.Entry(frm, textvariable=self.file_var, width=40)
        self.file_entry.grid(row=r, column=1, columnspan=3, sticky="ew", pady=(6, 0))
        self.file_btn = ttk.Button(frm, text="Browse…", command=self.browse_file)
        self.file_btn.grid(row=r, column=4, pady=(6, 0))

        # ---- row: terminal commands (shown only for Terminal mode) ----
        r += 1
        self.term_label = ttk.Label(frm, text="Commands:")
        self.term_label.grid(row=r, column=0, sticky="ne", padx=(0, 4), pady=(6, 0))
        self.term_cmds = tk.Text(frm, height=5, width=52)
        self.term_cmds.insert("1.0", "sig\n")
        self.term_cmds.grid(row=r, column=1, columnspan=4, sticky="ew", pady=(6, 0))

        self.term_hint_label = ttk.Label(frm, text=TERMINAL_HINT,
                                          justify="left", foreground="gray",
                                          wraplength=480)
        r += 1
        self.term_hint_label.grid(row=r, column=1, columnspan=4, sticky="w")

        # ---- separator ----
        r += 1
        ttk.Separator(frm).grid(row=r, column=0, columnspan=6, sticky="ew", pady=8)

        # ---- checkboxes + extra args ----
        r += 1
        self.disable_verify_var = tk.BooleanVar()
        ttk.Checkbutton(frm, text="Disable auto-verify (-V)",
                         variable=self.disable_verify_var).grid(row=r, column=0, columnspan=3, sticky="w")
        self.disable_erase_var = tk.BooleanVar()
        ttk.Checkbutton(frm, text="Disable chip erase (-D)",
                         variable=self.disable_erase_var).grid(row=r, column=3, columnspan=3, sticky="w")

        r += 1
        ttk.Label(frm, text="Extra args:").grid(row=r, column=0, sticky="e", padx=(0, 4), pady=(6, 0))
        self.extra_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.extra_var, width=60).grid(
            row=r, column=1, columnspan=5, sticky="ew", pady=(6, 0))

        # ---- command preview + run ----
        r += 1
        ttk.Separator(frm).grid(row=r, column=0, columnspan=6, sticky="ew", pady=8)
        r += 1
        ttk.Label(frm, text="Command:").grid(row=r, column=0, sticky="e", padx=(0, 4))
        self.cmd_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.cmd_var, state="readonly").grid(
            row=r, column=1, columnspan=5, sticky="ew")

        r += 1
        btns = ttk.Frame(frm)
        btns.grid(row=r, column=0, columnspan=6, sticky="w", pady=8)
        ttk.Button(btns, text="Update preview",
                   command=self.update_command_preview).pack(side="left", padx=4)
        ttk.Button(btns, text="▶  Run",
                   command=self.run_avrdude).pack(side="left", padx=4)

        for c in range(6):
            frm.columnconfigure(c, weight=1 if c in (1, 4) else 0)

        # auto-update preview
        for v in (self.programmer_var, self.part_var, self.port_var, self.baud_var,
                  self.conf_var, self.op_var, self.format_var, self.file_var,
                  self.disable_verify_var, self.disable_erase_var, self.extra_var):
            v.trace_add("write", lambda *_: self.update_command_preview())

        # initialise visibility
        self._on_op_change()

    # -------------------------------------------------------------- op change

    def _is_terminal_mode(self):
        return self.op_var.get() == OP_TERMINAL

    def _is_connect_mode(self):
        return self.op_var.get() == OP_CONNECT

    def _needs_file(self):
        """True for memory operations that require a file (r/w, not verify)."""
        op = self.op_var.get()
        for label, _, op_char in MEM_OPERATIONS:
            if label == op and op_char in ("r", "w"):
                return True
        return False

    def _on_op_change(self):
        """Show/hide file row and terminal-commands row based on selection."""
        is_term = self._is_terminal_mode()
        is_conn = self._is_connect_mode()
        is_mem  = not is_term and not is_conn

        # terminal command box
        state_term = "normal" if is_term else "disabled"
        self.term_cmds.configure(state=state_term)
        fg_hint = "gray" if is_term else "#b0b0b0"
        self.term_hint_label.configure(foreground=fg_hint)

        # file widgets
        state_file = "normal" if is_mem else "disabled"
        self.file_entry.configure(state=state_file)
        self.file_btn.configure(state=state_file)

        # format combo (only meaningful for memory ops)
        self.format_combo.configure(state="readonly" if is_mem else "disabled")

        self.update_command_preview()

    # ----------------------------------------------------------------- init

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
        self.conf_path    = conf
        if conf:
            self.conf_var.set(conf)
        self.status_var.set(f"avrdude: {path}")
        threading.Thread(target=self._load_lists, daemon=True).start()

    def _load_lists(self):
        progs, _ = query_avrdude_list(self.avrdude_path, self._conf_value(), "-c")
        parts, _ = query_avrdude_list(self.avrdude_path, self._conf_value(), "-p")
        self.programmers = sorted(progs)
        self.parts       = sorted(parts)

    def _conf_value(self):
        v = self.conf_var.get().strip()
        return v if v else self.conf_path

    # -------------------------------------------------------------- pickers

    def pick_programmer(self):
        if not self.programmers:
            messagebox.showinfo("Please wait",
                "Programmer list is still loading, or avrdude could not be queried.")
            return
        dlg = PickerDialog(self, "Select programmer", self.programmers)
        self.wait_window(dlg)
        if dlg.result:
            self.programmer_var.set(dlg.result)

    def pick_part(self):
        if not self.parts:
            messagebox.showinfo("Please wait",
                "Part list is still loading, or avrdude could not be queried.")
            return
        dlg = PickerDialog(self, "Select MCU / part", self.parts)
        self.wait_window(dlg)
        if dlg.result:
            self.part_var.set(dlg.result)

    def browse_file(self):
        if "Write" in self.op_var.get():
            f = filedialog.askopenfilename()
        else:
            f = filedialog.asksaveasfilename()
        if f:
            self.file_var.set(f)

    def browse_conf(self):
        f = filedialog.askopenfilename(
            filetypes=[("avrdude conf", "avrdude.conf"), ("All files", "*.*")]
        )
        if f:
            self.conf_var.set(f)

    # -------------------------------------------------------------- command

    def _base_args(self):
        """Build the common part of the avrdude command (no -U / -t)."""
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

        extra = self.extra_var.get().strip()
        if extra:
            args += extra.split()

        return args

    def build_args(self):
        """Full argument list for the currently selected operation."""
        args = self._base_args()
        op   = self.op_var.get()

        if op == OP_CONNECT:
            # no -U, avrdude just connects, reports chip id, then exits
            pass

        elif op == OP_TERMINAL:
            args.append("-t")

        else:
            # memory operation -> -U memtype:op:file:fmt
            memtype = op_char = None
            for label, mem, o in MEM_OPERATIONS:
                if label == op:
                    memtype, op_char = mem, o
                    break
            if memtype:
                fmt   = (self.format_var.get().split()[0]
                         if self.format_var.get() else "i")
                fname = self.file_var.get().strip()
                if op_char in ("r", "w") and not fname:
                    raise RuntimeError("Please choose a file for this operation.")
                target = fname if fname else "-"
                args += ["-U", f"{memtype}:{op_char}:{target}:{fmt}"]

        return args

    def update_command_preview(self):
        try:
            args = self.build_args()
            self.cmd_var.set(" ".join(self._quote(a) for a in args))
        except Exception as e:
            self.cmd_var.set(f"<{e}>")

    @staticmethod
    def _quote(s):
        return f'"{s}"' if (" " in s or s == "") else s

    # -------------------------------------------------------------- running

    def run_avrdude(self):
        if self.proc is not None:
            messagebox.showwarning("Busy", "A command is already running.")
            return
        try:
            args = self.build_args()
        except Exception as e:
            messagebox.showerror("Error", str(e))
            return

        self.update_command_preview()

        # For terminal mode, gather the commands to pipe to stdin
        stdin_text = None
        if self._is_terminal_mode():
            cmds = [c for c in self.term_cmds.get("1.0", "end").splitlines()
                    if c.strip()]
            if not any(c.strip().lower() in ("q", "quit") for c in cmds):
                cmds.append("quit")
            stdin_text = "\n".join(cmds) + "\n"
            for c in cmds:
                self._append(f">>> {c}\n")

        self._append(f"$ {' '.join(args)}\n")
        self.status_var.set("Running…")
        self.btn_cancel.configure(state="normal")

        def worker():
            try:
                self.proc = subprocess.Popen(
                    args,
                    stdin=subprocess.PIPE if stdin_text else subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
            except Exception as exc:
                self.out_queue.put(("line", f"Failed to start avrdude: {exc}\n"))
                self.out_queue.put(("done", None))
                return

            if stdin_text:
                try:
                    self.proc.stdin.write(stdin_text)
                    self.proc.stdin.close()
                except Exception:
                    pass

            for line in self.proc.stdout:
                self.out_queue.put(("line", line))

            self.proc.wait()
            self.out_queue.put(
                ("line", f"\n[process exited with code {self.proc.returncode}]\n")
            )
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

    # -------------------------------------------------------------- output

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
