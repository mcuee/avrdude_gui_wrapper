#!/usr/bin/env python3
"""
avrdude GUI (PySide6 version)
==============================

A single-file PySide6 front-end for `avrdude`, including support for
avrdude's interactive "terminal mode" (-t).

Requires: PySide6   (pip install PySide6)

Binary / config location strategy:

  Windows:
    1. avrdude.exe / avrdude.conf next to this script/exe.
    2. Fall back to PATH.
    -> error dialog if neither found.

  macOS / Linux:
    1. `avrdude` on PATH (avrdude.conf found by avrdude itself, or
       overridden via the "Config file" field).
    -> error dialog if not found.

Programmer / MCU lists are obtained by running `avrdude -c ?` and
`avrdude -p ?` against the located binary (same approach as the official
avrdude Python GUI), then shown in a filterable picker dialog.
"""

import os
import sys
import shutil
import platform
import subprocess

from PySide6.QtCore import Qt, QProcess, QTimer
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QLineEdit, QComboBox, QPushButton, QCheckBox, QTextEdit,
    QPlainTextEdit, QFileDialog, QMessageBox, QTabWidget, QGroupBox,
    QDialog, QDialogButtonBox, QTreeWidget, QTreeWidgetItem, QStatusBar,
)


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
    """Run `avrdude [-C conf] -c ?` / `-p ?` and parse "id = description" lines."""
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
        line = line.rstrip()
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
# Filterable picker dialog
# --------------------------------------------------------------------------

class PickerDialog(QDialog):
    def __init__(self, parent, title, items):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(560, 440)
        self.items = items
        self.result_value = None

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Filter:"))
        self.filter_edit = QLineEdit()
        self.filter_edit.textChanged.connect(self.refresh)
        layout.addWidget(self.filter_edit)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["ID", "Description"])
        self.tree.setColumnWidth(0, 160)
        self.tree.itemDoubleClicked.connect(self.accept)
        layout.addWidget(self.tree)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.refresh()
        self.filter_edit.setFocus()

    def refresh(self):
        f = self.filter_edit.text().strip().lower()
        self.tree.clear()
        for ident, desc in self.items:
            if f and f not in ident.lower() and f not in desc.lower():
                continue
            QTreeWidgetItem(self.tree, [ident, desc])

    def accept(self):
        item = self.tree.currentItem()
        if item:
            self.result_value = item.text(0)
        super().accept()


# --------------------------------------------------------------------------
# Main window
# --------------------------------------------------------------------------

OPERATIONS = [
    ("Read flash to file",     "flash",  "r"),
    ("Write flash from file",  "flash",  "w"),
    ("Verify flash",           "flash",  "v"),
    ("Read EEPROM to file",    "eeprom", "r"),
    ("Write EEPROM from file", "eeprom", "w"),
    ("Verify EEPROM",          "eeprom", "v"),
]

FORMATS = ["i (Intel Hex)", "r (raw binary)", "s (Motorola S-record)",
           "e (ELF, write only)"]


class AvrdudeGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("avrdude GUI (PySide6)")
        self.resize(840, 700)

        self.avrdude_path = None
        self.conf_path = None
        self.programmers = []
        self.parts = []
        self.process = None

        self._build_ui()
        self._locate_avrdude()

    # ------------------------------------------------------------------ UI

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)

        self.tabs = QTabWidget()
        outer.addWidget(self.tabs)

        self.tab_main = QWidget()
        self.tab_terminal = QWidget()
        self.tabs.addTab(self.tab_main, "Program")
        self.tabs.addTab(self.tab_terminal, "Terminal mode")

        self._build_main_tab()
        self._build_terminal_tab()

        # Output box (shared)
        out_box = QGroupBox("Output")
        out_layout = QVBoxLayout(out_box)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        font = self.output.font()
        font.setFamily("Consolas" if platform.system() == "Windows" else "Menlo")
        self.output.setFont(font)
        out_layout.addWidget(self.output)

        btn_row = QHBoxLayout()
        btn_copy = QPushButton("Copy")
        btn_copy.clicked.connect(self.copy_output)
        btn_clear = QPushButton("Clear")
        btn_clear.clicked.connect(self.output.clear)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self.cancel_run)
        btn_row.addWidget(btn_copy)
        btn_row.addWidget(btn_clear)
        btn_row.addStretch()
        btn_row.addWidget(self.btn_cancel)
        out_layout.addLayout(btn_row)

        outer.addWidget(out_box)

        self.status = QStatusBar()
        self.setStatusBar(self.status)

    def _build_main_tab(self):
        layout = QGridLayout(self.tab_main)
        r = 0

        layout.addWidget(QLabel("Programmer (-c):"), r, 0)
        self.programmer_edit = QLineEdit()
        layout.addWidget(self.programmer_edit, r, 1)
        btn_prog = QPushButton("Select...")
        btn_prog.clicked.connect(self.pick_programmer)
        layout.addWidget(btn_prog, r, 2)

        layout.addWidget(QLabel("MCU (-p):"), r, 3)
        self.part_edit = QLineEdit()
        layout.addWidget(self.part_edit, r, 4)
        btn_part = QPushButton("Select...")
        btn_part.clicked.connect(self.pick_part)
        layout.addWidget(btn_part, r, 5)

        r += 1
        layout.addWidget(QLabel("Port (-P):"), r, 0)
        self.port_combo = QComboBox()
        self.port_combo.setEditable(True)
        self.port_combo.addItems(self._guess_ports())
        self.port_combo.setCurrentText("usb")
        layout.addWidget(self.port_combo, r, 1)

        layout.addWidget(QLabel("Baud (-b):"), r, 3)
        self.baud_edit = QLineEdit()
        layout.addWidget(self.baud_edit, r, 4)

        r += 1
        layout.addWidget(QLabel("Config file (-C, optional):"), r, 0)
        self.conf_edit = QLineEdit()
        layout.addWidget(self.conf_edit, r, 1, 1, 4)
        btn_conf = QPushButton("Browse...")
        btn_conf.clicked.connect(self.browse_conf)
        layout.addWidget(btn_conf, r, 5)

        r += 1
        layout.addWidget(QLabel("Operation:"), r, 0)
        self.op_combo = QComboBox()
        self.op_combo.addItems([o[0] for o in OPERATIONS])
        layout.addWidget(self.op_combo, r, 1, 1, 2)

        layout.addWidget(QLabel("Format (-i/o):"), r, 3)
        self.format_combo = QComboBox()
        self.format_combo.addItems(FORMATS)
        layout.addWidget(self.format_combo, r, 4)

        r += 1
        layout.addWidget(QLabel("File:"), r, 0)
        self.file_edit = QLineEdit()
        layout.addWidget(self.file_edit, r, 1, 1, 4)
        btn_file = QPushButton("Browse...")
        btn_file.clicked.connect(self.browse_file)
        layout.addWidget(btn_file, r, 5)

        r += 1
        self.disable_verify_check = QCheckBox("Disable auto-verify (-V)")
        layout.addWidget(self.disable_verify_check, r, 0, 1, 2)
        self.disable_erase_check = QCheckBox("Disable chip erase (-D)")
        layout.addWidget(self.disable_erase_check, r, 2, 1, 2)

        r += 1
        layout.addWidget(QLabel("Extra args:"), r, 0)
        self.extra_edit = QLineEdit()
        layout.addWidget(self.extra_edit, r, 1, 1, 5)

        r += 1
        layout.addWidget(QLabel("Command:"), r, 0)
        self.cmd_preview = QLineEdit()
        self.cmd_preview.setReadOnly(True)
        layout.addWidget(self.cmd_preview, r, 1, 1, 5)

        r += 1
        btn_row = QHBoxLayout()
        btn_update = QPushButton("Update command preview")
        btn_update.clicked.connect(self.update_command_preview)
        btn_run = QPushButton("Run")
        btn_run.clicked.connect(self.run_avrdude)
        btn_row.addWidget(btn_update)
        btn_row.addWidget(btn_run)
        btn_row.addStretch()
        layout.addLayout(btn_row, r, 0, 1, 6)

        # auto-update preview on edits
        for w in (self.programmer_edit, self.part_edit, self.baud_edit,
                  self.conf_edit, self.file_edit, self.extra_edit):
            w.textChanged.connect(self.update_command_preview)
        for w in (self.port_combo, self.op_combo, self.format_combo):
            w.currentTextChanged.connect(self.update_command_preview)
        for w in (self.disable_verify_check, self.disable_erase_check):
            w.stateChanged.connect(self.update_command_preview)

        layout.setRowStretch(r + 1, 1)

    def _build_terminal_tab(self):
        layout = QVBoxLayout(self.tab_terminal)

        info = QLabel(
            "Terminal mode runs `avrdude -t ...` and sends the commands below "
            "to its interactive prompt (one per line). Common commands: "
            "d/dump, w/write, p/part, sig, q/quit."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        layout.addWidget(QLabel("Commands (one per line, 'quit' is appended automatically):"))
        self.term_cmds = QPlainTextEdit()
        self.term_cmds.setPlainText("sig\n")
        self.term_cmds.setMaximumHeight(160)
        layout.addWidget(self.term_cmds)

        btn_run_term = QPushButton("Run terminal session")
        btn_run_term.clicked.connect(self.run_terminal)
        layout.addWidget(btn_run_term, alignment=Qt.AlignLeft)

        layout.addStretch()

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
            QMessageBox.critical(self, "avrdude not found", err)
            self.status.showMessage("avrdude not found")
            return
        self.avrdude_path = path
        self.conf_path = conf
        if conf:
            self.conf_edit.setText(conf)
        self.status.showMessage(f"avrdude: {path}")

        # Defer list-loading slightly so the window shows first
        QTimer.singleShot(50, self._load_lists)

    def _load_lists(self):
        progs, _ = query_avrdude_list(self.avrdude_path, self.conf_path_value(), "-c")
        parts, _ = query_avrdude_list(self.avrdude_path, self.conf_path_value(), "-p")
        self.programmers = sorted(progs)
        self.parts = sorted(parts)

    def conf_path_value(self):
        v = self.conf_edit.text().strip()
        return v if v else self.conf_path

    # -------------------------------------------------------------- pickers

    def pick_programmer(self):
        if not self.programmers:
            QMessageBox.information(self, "Please wait",
                                     "Programmer list is still loading, or avrdude could not be queried.")
            return
        dlg = PickerDialog(self, "Select programmer", self.programmers)
        if dlg.exec() == QDialog.Accepted and dlg.result_value:
            self.programmer_edit.setText(dlg.result_value)

    def pick_part(self):
        if not self.parts:
            QMessageBox.information(self, "Please wait",
                                     "Part list is still loading, or avrdude could not be queried.")
            return
        dlg = PickerDialog(self, "Select MCU", self.parts)
        if dlg.exec() == QDialog.Accepted and dlg.result_value:
            self.part_edit.setText(dlg.result_value)

    def browse_file(self):
        if "Write" in self.op_combo.currentText():
            f, _ = QFileDialog.getOpenFileName(self, "Select file")
        else:
            f, _ = QFileDialog.getSaveFileName(self, "Select file")
        if f:
            self.file_edit.setText(f)

    def browse_conf(self):
        f, _ = QFileDialog.getOpenFileName(self, "Select avrdude.conf",
                                            filter="avrdude.conf;;All files (*)")
        if f:
            self.conf_edit.setText(f)

    # -------------------------------------------------------------- command

    def build_args(self):
        if not self.avrdude_path:
            raise RuntimeError("avrdude was not found. See the error shown at startup.")

        args = [self.avrdude_path]

        conf = self.conf_edit.text().strip()
        if conf:
            args += ["-C", conf]

        prog = self.programmer_edit.text().strip()
        if prog:
            args += ["-c", prog]

        part = self.part_edit.text().strip()
        if part:
            args += ["-p", part]

        port = self.port_combo.currentText().strip()
        if port:
            args += ["-P", port]

        baud = self.baud_edit.text().strip()
        if baud:
            args += ["-b", baud]

        if self.disable_verify_check.isChecked():
            args.append("-V")
        if self.disable_erase_check.isChecked():
            args.append("-D")

        op_label = self.op_combo.currentText()
        memtype, op = None, None
        for label, mem, o in OPERATIONS:
            if label == op_label:
                memtype, op = mem, o
                break

        fmt_full = self.format_combo.currentText()
        fmt = fmt_full.split()[0] if fmt_full else "i"

        fname = self.file_edit.text().strip()
        if memtype and op:
            if op in ("r", "w") and not fname:
                raise RuntimeError("Please choose a file for this operation.")
            target = fname if fname else "-"
            args += ["-U", f"{memtype}:{op}:{target}:{fmt}"]

        extra = self.extra_edit.text().strip()
        if extra:
            args += extra.split()

        return args

    def update_command_preview(self):
        try:
            args = self.build_args()
            self.cmd_preview.setText(" ".join(self._quote(a) for a in args))
        except Exception as e:
            self.cmd_preview.setText(f"<{e}>")

    @staticmethod
    def _quote(s):
        if " " in s or s == "":
            return f'"{s}"'
        return s

    # -------------------------------------------------------------- running

    def _start_process(self, args, stdin_text=None):
        if self.process is not None:
            QMessageBox.warning(self, "Busy", "A command is already running.")
            return

        self.append_output(f"$ {' '.join(args)}\n")
        self.status.showMessage("Running...")
        self.btn_cancel.setEnabled(True)

        proc = QProcess(self)
        proc.setProgram(args[0])
        proc.setArguments(args[1:])
        proc.setProcessChannelMode(QProcess.MergedChannels)

        proc.readyReadStandardOutput.connect(lambda: self._on_ready(proc))
        proc.finished.connect(lambda code, status: self._on_finished(code, status))
        proc.errorOccurred.connect(lambda err: self._on_error(err))

        self.process = proc
        proc.start()

        if stdin_text is not None:
            if not proc.waitForStarted(3000):
                self._on_error(proc.error())
                return
            proc.write(stdin_text.encode("utf-8"))
            proc.closeWriteChannel()

    def _on_ready(self, proc):
        data = bytes(proc.readAllStandardOutput()).decode("utf-8", errors="replace")
        self.append_output(data)

    def _on_finished(self, code, _status):
        self.append_output(f"\n[process exited with code {code}]\n")
        self.process = None
        self.status.showMessage("Idle")
        self.btn_cancel.setEnabled(False)

    def _on_error(self, err):
        self.append_output(f"\n[process error: {err}]\n")
        self.process = None
        self.status.showMessage("Idle")
        self.btn_cancel.setEnabled(False)

    def run_avrdude(self):
        try:
            args = self.build_args()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
            return
        self.update_command_preview()
        self._start_process(args)

    def run_terminal(self):
        cmds = [c for c in self.term_cmds.toPlainText().splitlines() if c.strip()]
        if not any(c.strip().lower() in ("q", "quit") for c in cmds):
            cmds.append("quit")
        stdin_text = "\n".join(cmds) + "\n"

        try:
            args = self.build_args()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
            return
        if "-t" not in args:
            args.append("-t")

        for c in cmds:
            self.append_output(f">>> {c}\n")
        self._start_process(args, stdin_text=stdin_text)

    def cancel_run(self):
        if self.process is not None:
            self.process.terminate()

    # -------------------------------------------------------------- output

    def append_output(self, text):
        self.output.moveCursor(self.output.textCursor().End)
        self.output.insertPlainText(text)
        self.output.moveCursor(self.output.textCursor().End)

    def copy_output(self):
        QApplication.clipboard().setText(self.output.toPlainText())


def main():
    app = QApplication(sys.argv)
    win = AvrdudeGUI()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
