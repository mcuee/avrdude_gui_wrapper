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
avrdude Python GUI), then shown in a filterable picker dialog with
separate ID and Description filter fields.

Theme support: Light, Dark, System (auto-follow OS preference).
"""

import os
import sys
import shutil
import platform
import subprocess

from PySide6.QtCore import Qt, QProcess, QTimer, QSettings
from PySide6.QtGui import QPalette, QColor
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QLineEdit, QComboBox, QPushButton, QCheckBox,
    QPlainTextEdit, QFileDialog, QMessageBox, QTabWidget, QGroupBox,
    QDialog, QDialogButtonBox, QTreeWidget, QTreeWidgetItem, QStatusBar,
    QSizePolicy,
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
# Theme management
# --------------------------------------------------------------------------

THEME_SYSTEM = "System"
THEME_LIGHT  = "Light"
THEME_DARK   = "Dark"
THEMES       = [THEME_SYSTEM, THEME_LIGHT, THEME_DARK]

# A clean, readable dark palette
_DARK_PALETTE = {
    QPalette.Window:          "#2b2b2b",
    QPalette.WindowText:      "#f0f0f0",
    QPalette.Base:            "#1e1e1e",
    QPalette.AlternateBase:   "#2b2b2b",
    QPalette.ToolTipBase:     "#1e1e1e",
    QPalette.ToolTipText:     "#f0f0f0",
    QPalette.Text:            "#f0f0f0",
    QPalette.Button:          "#3c3f41",
    QPalette.ButtonText:      "#f0f0f0",
    QPalette.BrightText:      "#ff6b6b",
    QPalette.Link:            "#5294e2",
    QPalette.Highlight:       "#4a90d9",
    QPalette.HighlightedText: "#ffffff",
    QPalette.PlaceholderText: "#888888",
}

_DARK_DISABLED = {
    QPalette.Text:       "#666666",
    QPalette.ButtonText: "#666666",
    QPalette.WindowText: "#666666",
}

# A clean light palette (explicit so switching back from dark is clean)
_LIGHT_PALETTE = {
    QPalette.Window:          "#f0f0f0",
    QPalette.WindowText:      "#1a1a1a",
    QPalette.Base:            "#ffffff",
    QPalette.AlternateBase:   "#f7f7f7",
    QPalette.ToolTipBase:     "#ffffdc",
    QPalette.ToolTipText:     "#1a1a1a",
    QPalette.Text:            "#1a1a1a",
    QPalette.Button:          "#e0e0e0",
    QPalette.ButtonText:      "#1a1a1a",
    QPalette.BrightText:      "#cc0000",
    QPalette.Link:            "#0057ae",
    QPalette.Highlight:       "#308cc6",
    QPalette.HighlightedText: "#ffffff",
    QPalette.PlaceholderText: "#999999",
}

_LIGHT_DISABLED = {
    QPalette.Text:       "#a0a0a0",
    QPalette.ButtonText: "#a0a0a0",
    QPalette.WindowText: "#a0a0a0",
}


def _build_palette(color_map, disabled_map):
    pal = QPalette()
    for role, color in color_map.items():
        pal.setColor(QPalette.Active,   role, QColor(color))
        pal.setColor(QPalette.Inactive, role, QColor(color))
    for role, color in disabled_map.items():
        pal.setColor(QPalette.Disabled, role, QColor(color))
    return pal


def apply_theme(app: QApplication, theme: str):
    """Apply Light / Dark / System theme to the QApplication."""
    app.setStyle("Fusion")          # Fusion renders palettes faithfully on all OS
    if theme == THEME_DARK:
        app.setPalette(_build_palette(_DARK_PALETTE, _DARK_DISABLED))
        app.setStyleSheet(
            "QToolTip { color: #f0f0f0; background-color: #1e1e1e; "
            "border: 1px solid #555; }"
        )
    elif theme == THEME_LIGHT:
        app.setPalette(_build_palette(_LIGHT_PALETTE, _LIGHT_DISABLED))
        app.setStyleSheet("")
    else:  # System
        app.setPalette(app.style().standardPalette())
        app.setStyleSheet("")


# --------------------------------------------------------------------------
# Filterable picker dialog  (separate ID + Description filter fields)
# --------------------------------------------------------------------------

class PickerDialog(QDialog):
    """
    Shows a two-column (ID / Description) list with *two* independent filter
    boxes so the user can narrow by ID alone, description alone, or both at
    once.
    """

    def __init__(self, parent, title, items):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(600, 480)
        self.items = items
        self.result_value = None

        layout = QVBoxLayout(self)

        # ---- dual filter row ----
        filter_row = QHBoxLayout()

        filter_row.addWidget(QLabel("Filter ID:"))
        self.filter_id = QLineEdit()
        self.filter_id.setPlaceholderText("e.g.  arduino")
        self.filter_id.textChanged.connect(self._refresh)
        filter_row.addWidget(self.filter_id, 1)

        filter_row.addSpacing(12)

        filter_row.addWidget(QLabel("Filter description:"))
        self.filter_desc = QLineEdit()
        self.filter_desc.setPlaceholderText("e.g.  Uno")
        self.filter_desc.textChanged.connect(self._refresh)
        filter_row.addWidget(self.filter_desc, 2)

        btn_clear = QPushButton("Clear")
        btn_clear.setFixedWidth(54)
        btn_clear.clicked.connect(self._clear_filters)
        filter_row.addWidget(btn_clear)

        layout.addLayout(filter_row)

        # ---- result count label ----
        self.count_label = QLabel()
        layout.addWidget(self.count_label)

        # ---- tree ----
        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["ID", "Description"])
        self.tree.setColumnWidth(0, 170)
        self.tree.setSortingEnabled(True)
        self.tree.sortByColumn(0, Qt.AscendingOrder)
        self.tree.itemDoubleClicked.connect(self._accept_current)
        layout.addWidget(self.tree)

        # ---- buttons ----
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept_current)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._refresh()
        self.filter_id.setFocus()

    def _clear_filters(self):
        self.filter_id.clear()
        self.filter_desc.clear()
        self.filter_id.setFocus()

    def _refresh(self):
        fi = self.filter_id.text().strip().lower()
        fd = self.filter_desc.text().strip().lower()

        self.tree.clear()
        count = 0
        for ident, desc in self.items:
            if fi and fi not in ident.lower():
                continue
            if fd and fd not in desc.lower():
                continue
            QTreeWidgetItem(self.tree, [ident, desc])
            count += 1

        total = len(self.items)
        self.count_label.setText(
            f"{count} of {total} entries" if (fi or fd) else f"{total} entries"
        )

    def _accept_current(self):
        item = self.tree.currentItem()
        if item:
            self.result_value = item.text(0)
            self.accept()


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

FORMATS = [
    "i (Intel Hex)",
    "r (raw binary)",
    "s (Motorola S-record)",
    "e (ELF, write only)",
]

SETTINGS_ORG  = "avrdude-gui"
SETTINGS_APP  = "avrdude-gui-qt"
KEY_THEME     = "theme"


class AvrdudeGUI(QMainWindow):
    def __init__(self, app: QApplication):
        super().__init__()
        self.app = app
        self.setWindowTitle("avrdude GUI (PySide6)")
        self.resize(860, 720)

        self.avrdude_path = None
        self.conf_path    = None
        self.programmers  = []
        self.parts        = []
        self.process      = None

        self.settings = QSettings(SETTINGS_ORG, SETTINGS_APP)

        self._build_ui()
        self._locate_avrdude()

    # ------------------------------------------------------------------ UI

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        self.tabs = QTabWidget()
        outer.addWidget(self.tabs)

        self.tab_main     = QWidget()
        self.tab_terminal = QWidget()
        self.tabs.addTab(self.tab_main,     "Program")
        self.tabs.addTab(self.tab_terminal, "Terminal mode")

        self._build_main_tab()
        self._build_terminal_tab()

        # ---- shared output box ----
        out_box = QGroupBox("Output")
        out_layout = QVBoxLayout(out_box)
        out_layout.setContentsMargins(6, 6, 6, 6)

        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        font = self.output.font()
        font.setFamily("Consolas" if platform.system() == "Windows" else "Menlo")
        font.setPointSize(10)
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

        outer.addWidget(out_box, stretch=1)

        # ---- status bar ----
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self._build_statusbar_theme_picker()

    def _build_statusbar_theme_picker(self):
        """Embed a compact theme selector in the right side of the status bar."""
        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 4, 0)
        row.setSpacing(4)

        row.addWidget(QLabel("Theme:"))
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(THEMES)
        self.theme_combo.setFixedWidth(90)

        saved = self.settings.value(KEY_THEME, THEME_SYSTEM)
        idx = self.theme_combo.findText(saved)
        if idx >= 0:
            self.theme_combo.setCurrentIndex(idx)
        apply_theme(self.app, self.theme_combo.currentText())

        self.theme_combo.currentTextChanged.connect(self._on_theme_changed)
        row.addWidget(self.theme_combo)

        self.status.addPermanentWidget(container)

    def _on_theme_changed(self, theme: str):
        apply_theme(self.app, theme)
        self.settings.setValue(KEY_THEME, theme)

    # ---------------------------------------------------------------- tabs

    def _build_main_tab(self):
        layout = QGridLayout(self.tab_main)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setVerticalSpacing(6)
        layout.setHorizontalSpacing(6)
        r = 0

        # Row 0 – programmer / part
        layout.addWidget(QLabel("Programmer (-c):"), r, 0, Qt.AlignRight)
        self.programmer_edit = QLineEdit()
        layout.addWidget(self.programmer_edit, r, 1)
        btn_prog = QPushButton("Select…")
        btn_prog.clicked.connect(self.pick_programmer)
        layout.addWidget(btn_prog, r, 2)

        layout.addWidget(QLabel("MCU (-p):"), r, 3, Qt.AlignRight)
        self.part_edit = QLineEdit()
        layout.addWidget(self.part_edit, r, 4)
        btn_part = QPushButton("Select…")
        btn_part.clicked.connect(self.pick_part)
        layout.addWidget(btn_part, r, 5)

        # Row 1 – port / baud
        r += 1
        layout.addWidget(QLabel("Port (-P):"), r, 0, Qt.AlignRight)
        self.port_combo = QComboBox()
        self.port_combo.setEditable(True)
        self.port_combo.addItems(self._guess_ports())
        self.port_combo.setCurrentText("usb")
        layout.addWidget(self.port_combo, r, 1)

        layout.addWidget(QLabel("Baud (-b):"), r, 3, Qt.AlignRight)
        self.baud_edit = QLineEdit()
        layout.addWidget(self.baud_edit, r, 4)

        # Row 2 – config file
        r += 1
        layout.addWidget(QLabel("Config file (-C):"), r, 0, Qt.AlignRight)
        self.conf_edit = QLineEdit()
        self.conf_edit.setPlaceholderText("optional – leave blank to use avrdude default")
        layout.addWidget(self.conf_edit, r, 1, 1, 4)
        btn_conf = QPushButton("Browse…")
        btn_conf.clicked.connect(self.browse_conf)
        layout.addWidget(btn_conf, r, 5)

        # Row 3 – operation / format
        r += 1
        layout.addWidget(QLabel("Operation:"), r, 0, Qt.AlignRight)
        self.op_combo = QComboBox()
        self.op_combo.addItems([o[0] for o in OPERATIONS])
        layout.addWidget(self.op_combo, r, 1, 1, 2)

        layout.addWidget(QLabel("Format:"), r, 3, Qt.AlignRight)
        self.format_combo = QComboBox()
        self.format_combo.addItems(FORMATS)
        layout.addWidget(self.format_combo, r, 4)

        # Row 4 – file
        r += 1
        layout.addWidget(QLabel("File:"), r, 0, Qt.AlignRight)
        self.file_edit = QLineEdit()
        layout.addWidget(self.file_edit, r, 1, 1, 4)
        btn_file = QPushButton("Browse…")
        btn_file.clicked.connect(self.browse_file)
        layout.addWidget(btn_file, r, 5)

        # Row 5 – checkboxes
        r += 1
        self.disable_verify_check = QCheckBox("Disable auto-verify (-V)")
        layout.addWidget(self.disable_verify_check, r, 0, 1, 3)
        self.disable_erase_check = QCheckBox("Disable chip erase (-D)")
        layout.addWidget(self.disable_erase_check, r, 3, 1, 3)

        # Row 6 – extra args
        r += 1
        layout.addWidget(QLabel("Extra args:"), r, 0, Qt.AlignRight)
        self.extra_edit = QLineEdit()
        self.extra_edit.setPlaceholderText("any additional avrdude flags")
        layout.addWidget(self.extra_edit, r, 1, 1, 5)

        # Row 7 – command preview
        r += 1
        layout.addWidget(QLabel("Command:"), r, 0, Qt.AlignRight)
        self.cmd_preview = QLineEdit()
        self.cmd_preview.setReadOnly(True)
        layout.addWidget(self.cmd_preview, r, 1, 1, 5)

        # Row 8 – buttons
        r += 1
        btn_row_w = QWidget()
        btn_row = QHBoxLayout(btn_row_w)
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_update = QPushButton("Update preview")
        btn_update.clicked.connect(self.update_command_preview)
        btn_run = QPushButton("▶  Run")
        btn_run.setDefault(True)
        btn_run.clicked.connect(self.run_avrdude)
        btn_row.addWidget(btn_update)
        btn_row.addWidget(btn_run)
        btn_row.addStretch()
        layout.addWidget(btn_row_w, r, 0, 1, 6)

        layout.setRowStretch(r + 1, 1)
        layout.setColumnStretch(1, 2)
        layout.setColumnStretch(4, 2)

        # auto-update preview
        for w in (self.programmer_edit, self.part_edit, self.baud_edit,
                  self.conf_edit, self.file_edit, self.extra_edit):
            w.textChanged.connect(self.update_command_preview)
        for w in (self.port_combo, self.op_combo, self.format_combo):
            w.currentTextChanged.connect(self.update_command_preview)
        for w in (self.disable_verify_check, self.disable_erase_check):
            w.stateChanged.connect(self.update_command_preview)

    def _build_terminal_tab(self):
        layout = QVBoxLayout(self.tab_terminal)
        layout.setContentsMargins(8, 8, 8, 8)

        info = QLabel(
            "Terminal mode runs <b>avrdude -t …</b> and feeds the commands "
            "below to its interactive prompt, one per line.<br>"
            "Common commands: <tt>sig</tt>, <tt>dump flash 0 64</tt>, "
            "<tt>write eeprom 0 0xff</tt>, <tt>part</tt>, <tt>quit</tt>."
        )
        info.setWordWrap(True)
        info.setTextFormat(Qt.RichText)
        layout.addWidget(info)

        layout.addSpacing(4)
        layout.addWidget(QLabel("Commands (one per line — 'quit' is appended automatically):"))
        self.term_cmds = QPlainTextEdit()
        self.term_cmds.setPlainText("sig\n")
        self.term_cmds.setMaximumHeight(180)
        layout.addWidget(self.term_cmds)

        btn_run_term = QPushButton("▶  Run terminal session")
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
        self.conf_path    = conf
        if conf:
            self.conf_edit.setText(conf)
        self.status.showMessage(f"avrdude: {path}")
        QTimer.singleShot(60, self._load_lists)

    def _load_lists(self):
        progs, _ = query_avrdude_list(self.avrdude_path, self._conf_value(), "-c")
        parts, _ = query_avrdude_list(self.avrdude_path, self._conf_value(), "-p")
        self.programmers = sorted(progs)
        self.parts       = sorted(parts)

    def _conf_value(self):
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
        dlg = PickerDialog(self, "Select MCU / part", self.parts)
        if dlg.exec() == QDialog.Accepted and dlg.result_value:
            self.part_edit.setText(dlg.result_value)

    def browse_file(self):
        if "Write" in self.op_combo.currentText():
            f, _ = QFileDialog.getOpenFileName(self, "Select input file")
        else:
            f, _ = QFileDialog.getSaveFileName(self, "Select output file")
        if f:
            self.file_edit.setText(f)

    def browse_conf(self):
        f, _ = QFileDialog.getOpenFileName(
            self, "Select avrdude.conf",
            filter="avrdude config (avrdude.conf);;All files (*)"
        )
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
        memtype = op = None
        for label, mem, o in OPERATIONS:
            if label == op_label:
                memtype, op = mem, o
                break

        fmt = (self.format_combo.currentText().split()[0]
               if self.format_combo.currentText() else "i")

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
        return f'"{s}"' if (" " in s or s == "") else s

    # -------------------------------------------------------------- running

    def _start_process(self, args, stdin_text=None):
        if self.process is not None:
            QMessageBox.warning(self, "Busy", "A command is already running.")
            return

        self.append_output(f"$ {' '.join(args)}\n")
        self.status.showMessage("Running…")
        self.btn_cancel.setEnabled(True)

        proc = QProcess(self)
        proc.setProgram(args[0])
        proc.setArguments(args[1:])
        proc.setProcessChannelMode(QProcess.MergedChannels)
        proc.readyReadStandardOutput.connect(lambda: self._on_ready(proc))
        proc.finished.connect(self._on_finished)
        proc.errorOccurred.connect(self._on_error)

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


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def main():
    app = QApplication(sys.argv)
    app.setOrganizationName(SETTINGS_ORG)
    app.setApplicationName(SETTINGS_APP)

    win = AvrdudeGUI(app)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
