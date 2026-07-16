"""Main window for the fabconvert GUI.

A clean, modern PySide6 app with:
  * drag & drop input files (.svg/.dxf + all 9 Gerber extensions) plus a picker,
  * output-format + destination selection, unit-override dropdown,
  * two side-by-side live ``GeometryCanvas`` panels (input / output-after),
  * an info/status strip (detected unit, detection_note, bbox, counts, notes),
  * friendly error dialogs (never raw tracebacks) for unrecognized extensions,
    ``UnsupportedGerberConstruct``, missing ezdxf, and corrupt files,
  * light/dark theme toggle.

All heavy work goes through :mod:`fabconvert.gui.convertworker` (pure, public
API only); this module only marshals Qt.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog,
                                QDialogButtonBox, QFileDialog, QFrame, QGroupBox,
                                QHBoxLayout, QLabel, QLineEdit, QListWidget,
                                QListWidgetItem, QMainWindow, QMessageBox,
                                QProgressBar, QPushButton, QSpinBox,
                                QStatusBar, QTabWidget, QTextEdit, QVBoxLayout,
                                QWidget)

from .. import __version__
from ..alignment import Alignment, Units
from ..core.geometry import GeometrySet
from . import theme
from .convertworker import (ConvertError, ConvertResult, convert_batch,
                            convert_one, load_file)
from .preview import GeometryCanvas

# Accepted input extensions (lowercase, dot-prefixed) — shared with cli.py.
SVG_EXTS = (".svg",)
DXF_EXTS = (".dxf",)
GERBER_EXTS = (".gbr", ".gtl", ".gbo", ".gbs", ".gbl", ".gto", ".gts",
               ".gko", ".gm1")
ALL_EXTS = SVG_EXTS + DXF_EXTS + GERBER_EXTS

_DROP_FILTER = " ".join(f"*{e}" for e in ALL_EXTS)
_PICK_FILTER = (
    "PCB fabrication files (*.svg *.dxf *.gbr *.gtl *.gbo *.gbs *.gbl "
    "*.gto *.gts *.gko *.gm1);;SVG (*.svg);;DXF (*.dxf);;"
    "Gerber (*.gbr *.gtl *.gbo *.gbs *.gbl *.gto *.gts *.gko *.gm1);;"
    "All files (*.*)"
)

_UNIT_ITEMS = [("Auto", None), ("mm", Units.MM), ("inch", Units.INCH),
               ("mil", Units.MIL), ("cm", Units.CM), ("m", Units.M)]
_OUT_FORMATS = [("SVG (.svg)", ".svg"), ("DXF (.dxf)", ".dxf"),
                ("Gerber (.gbr)", ".gbr")]


class _ErrorDialog(QDialog):
    """A friendly error dialog with a 'Details' expander for the traceback."""

    def __init__(self, title: str, message: str, parent: Optional[QWidget] = None
                 ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        v = QVBoxLayout(self)
        lbl = QLabel(message)
        lbl.setWordWrap(True)
        v.addWidget(lbl)
        details = QTextEdit()
        details.setReadOnly(True)
        details.setVisible(False)
        v.addWidget(details)
        bb = QDialogButtonBox(QDialogButtonBox.Ok)
        show_btn = QPushButton("Details…")
        bb.addButton(show_btn, QDialogButtonBox.ActionRole)
        v.addWidget(bb)

        def _toggle():
            details.setVisible(not details.isVisible())
            self.adjustSize()

        show_btn.clicked.connect(_toggle)
        bb.accepted.connect(self.accept)
        self._details = details


class DropArea(QFrame):
    """A drag&drop target listing accepted extensions."""

    def __init__(self, on_files, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.setAcceptDrops(True)
        self._on_files = on_files
        lay = QVBoxLayout(self)
        hint = QLabel("Drop .svg / .dxf / .gbr (+ .gtl .gbo .gbs .gbl "
                      ".gto .gts .gko .gm1) here")
        hint.setObjectName("DropHint")
        hint.setAlignment(Qt.AlignCenter)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.addWidget(hint)

    def dragEnterEvent(self, e) -> None:
        if e.mimeData().hasUrls():
            if any(Path(u.toLocalFile()).suffix.lower() in ALL_EXTS
                   for u in e.mimeData().urls()):
                e.acceptProposedAction()
                return
        e.ignore()

    def dropEvent(self, e) -> None:
        files = [Path(u.toLocalFile()) for u in e.mimeData().urls()
                 if Path(u.toLocalFile()).suffix.lower() in ALL_EXTS]
        files = [f for f in files if f.is_file()]
        if files:
            self._on_files(files)
            e.acceptProposedAction()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"fabconvert {__version__}")
        self.resize(1180, 760)
        self._files: List[Path] = []
        self._dark = False
        self._out_ext = ".gbr"

        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(14, 14, 14, 14)
        outer.setSpacing(12)

        # Header.
        header = QHBoxLayout()
        title = QLabel("fabconvert")
        title.setObjectName("Title")
        sub = QLabel("SVG ⇄ DXF ⇄ Gerber · drag, convert, preview")
        sub.setObjectName("Subtitle")
        header.addWidget(title)
        header.addWidget(sub)
        header.addStretch(1)
        self.theme_btn = QPushButton("🌙 Dark")
        self.theme_btn.clicked.connect(self._toggle_theme)
        header.addWidget(self.theme_btn)
        outer.addLayout(header)

        # Input row: drop area + list + browse/clear.
        in_group = QGroupBox("Input files")
        ig = QHBoxLayout(in_group)
        self.drop = DropArea(self._add_files)
        self.drop.setFixedHeight(96)
        ig.addWidget(self.drop, 1)
        right = QVBoxLayout()
        self.file_list = QListWidget()
        self.file_list.setSelectionMode(QListWidget.ExtendedSelection)
        self.file_list.currentRowChanged.connect(self._on_select)
        right.addWidget(self.file_list, 1)
        btns = QHBoxLayout()
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        clear = QPushButton("Clear")
        clear.clicked.connect(self._clear_files)
        btns.addWidget(browse)
        btns.addWidget(clear)
        right.addLayout(btns)
        ig.addLayout(right, 1)
        outer.addWidget(in_group)

        # Settings row.
        sg = QHBoxLayout()
        self.out_fmt = QComboBox()
        for label, _ in _OUT_FORMATS:
            self.out_fmt.addItem(label)
        self.out_fmt.currentIndexChanged.connect(self._on_fmt)
        sg.addWidget(QLabel("Output format:"))
        sg.addWidget(self.out_fmt)
        sg.addSpacing(10)
        self.out_dir_ed = QLineEdit()
        self.out_dir_ed.setPlaceholderText("Output directory…")
        outdir_btn = QPushButton("Directory…")
        outdir_btn.clicked.connect(self._pick_dir)
        sg.addWidget(QLabel("Output dir:"))
        sg.addWidget(self.out_dir_ed, 1)
        sg.addWidget(outdir_btn)
        sg.addSpacing(10)
        self.unit_cb = QComboBox()
        for label, _ in _UNIT_ITEMS:
            self.unit_cb.addItem(label)
        sg.addWidget(QLabel("Unit override:"))
        sg.addWidget(self.unit_cb)
        sg.addStretch(1)
        self.convert_btn = QPushButton("Convert")
        self.convert_btn.setObjectName("Primary")
        self.convert_btn.clicked.connect(self._do_convert)
        sg.addWidget(self.convert_btn)
        outer.addLayout(sg)

        # Preview canvases.
        pv = QHBoxLayout()
        self.input_canvas = GeometryCanvas("INPUT")
        self.output_canvas = GeometryCanvas("OUTPUT (after)")
        pv.addWidget(self.input_canvas, 1)
        pv.addWidget(self.output_canvas, 1)
        outer.addLayout(pv, 1)

        # Info + progress.
        info_group = QGroupBox("Info")
        ig2 = QVBoxLayout(info_group)
        self.info_lbl = QLabel("Select a file to see detected unit, "
                               "bounding box, and entity counts.")
        self.info_lbl.setWordWrap(True)
        ig2.addWidget(self.info_lbl)
        outer.addWidget(info_group)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        outer.addWidget(self.progress)

        self.tabs = QTabWidget()
        self.tabs.addTab(QWidget(), "Results")
        self.tabs.setVisible(False)
        outer.addWidget(self.tabs)

        self.setStatusBar(QStatusBar())
        self.setCentralWidget(central)

    # ---- files -----------------------------------------------------------

    def _add_files(self, files: List[Path]) -> None:
        existing = set(self._files)
        for f in files:
            if f not in existing:
                self._files.append(f)
                existing.add(f)
                self.file_list.addItem(QListWidgetItem(f.name))
        if self._files:
            self.file_list.setCurrentRow(0)

    def _browse(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Select input files", "", _PICK_FILTER)
        if paths:
            self._add_files([Path(p) for p in paths])

    def _clear_files(self) -> None:
        self._files.clear()
        self.file_list.clear()
        self.input_canvas.set_geometry(None)
        self.output_canvas.set_geometry(None)
        self.info_lbl.setText("Select a file to see detected unit, "
                              "bounding box, and entity counts.")

    def _on_select(self, row: int) -> None:
        if row < 0 or row >= len(self._files):
            return
        self._load_preview(self._files[row])

    def _load_preview(self, path: Path) -> None:
        unit = self._unit_override()
        res = load_file(path, unit_override=unit)
        if res.ok and res.geom is not None:
            self.input_canvas.set_geometry(res.geom)
            self.output_canvas.set_geometry(None)
            self._set_info(path, res.geom, res.alignment, res.notes)
            self.statusBar().showMessage(f"Loaded {path.name}", 4000)
        else:
            self.input_canvas.set_geometry(None)
            self.output_canvas.set_geometry(None)
            self._show_error("Could not open file", res.error or
                             ConvertError("Unknown error"))
            self.info_lbl.setText(f"Failed to read {path.name}.")

    # ---- settings --------------------------------------------------------

    def _on_fmt(self, idx: int) -> None:
        self._out_ext = _OUT_FORMATS[idx][1]

    def _pick_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Select output directory")
        if d:
            self.out_dir_ed.setText(d)

    def _unit_override(self) -> Optional[Units]:
        return _UNIT_ITEMS[self.unit_cb.currentIndex()][1]

    # ---- convert ---------------------------------------------------------

    def _do_convert(self) -> None:
        if not self._files:
            QMessageBox.information(self, "fabconvert",
                                     "Add some input files first (drag & drop "
                                     "or Browse).")
            return
        out_dir = self.out_dir_ed.text().strip()
        if not out_dir:
            QMessageBox.information(self, "fabconvert",
                                     "Choose an output directory.")
            return
        out_dir_path = Path(out_dir)
        out_dir_path.mkdir(parents=True, exist_ok=True)
        pairs = [(src, out_dir_path / f"{src.stem}{self._out_ext}")
                 for src in self._files]
        unit = self._unit_override()

        if len(pairs) == 1:
            self._convert_single(pairs[0], unit)
        else:
            self._convert_batch(pairs, unit)

    def _convert_single(self, pair: tuple, unit: Optional[Units]) -> None:
        src, dst = pair
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.convert_btn.setEnabled(False)
        res = convert_one(src, dst, unit_override=unit)
        self.convert_btn.setEnabled(True)
        self.progress.setVisible(False)
        if res.error is not None:
            self._show_error("Conversion failed", res.error,
                             captured=self._joined_notes(res))
            return
        # Show results.
        self.input_canvas.set_geometry(res.geom_in)
        self.output_canvas.set_geometry(res.geom_out)
        info = (f"Converted {src.name} → {dst.name}.  "
                f"detected unit: "
                f"{res.alignment_in.detected_unit.value if res.alignment_in and res.alignment_in.detected_unit else '—'}")
        if res.alignment_in and res.alignment_in.detection_note:
            info += (f"   • note: {res.alignment_in.detection_note}")
        info += (f"   • bbox: {self._fmt_bounds(res.geom_in.bounds())}")
        if res.polygons_dropped:
            info += (f"   • ⚠ {res.polygons_dropped} filled polygon(s) "
                     f"dropped (Gerber can't represent filled regions).")
        if res.notes_in or res.notes_out:
            info += (f"   • notes:\n"
                     f"{self._joined_notes(res)}")
        self.info_lbl.setText(info)
        self.statusBar().showMessage(f"✓ {dst.name} written", 6000)

    def _convert_batch(self, pairs: list,
                       unit: Optional[Units]) -> None:
        self.progress.setVisible(True)
        self.progress.setRange(0, len(pairs))
        self.convert_btn.setEnabled(False)
        results = convert_batch(pairs, unit_override=unit)
        self.convert_btn.setEnabled(True)
        self.progress.setVisible(False)
        # Results table in a fresh tab.
        from PySide6.QtWidgets import QTableWidget, QTableWidgetItem
        tab = QWidget()
        tl = QVBoxLayout(tab)
        tl.setContentsMargins(8, 8, 8, 8)
        ok = sum(1 for r in results if r.ok)
        tl.addWidget(QLabel(f"{ok}/{len(results)} converted."))
        tbl = QTableWidget(len(results), 4)
        tbl.setHorizontalHeaderLabels(["file", "output", "status", "reason"])
        for i, r in enumerate(results):
            tbl.setItem(i, 0, QTableWidgetItem(r.src.name))
            tbl.setItem(i, 1, QTableWidgetItem(r.dst.name))
            tbl.setItem(i, 2, QTableWidgetItem("✓" if r.ok else "✗"))
            tbl.setItem(i, 3,
                        QTableWidgetItem("" if r.ok else str(r.error)))
        tbl.resizeColumnsToContents()
        tl.addWidget(tbl)
        idx = self.tabs.addTab(tab, f"Batch ({ok}/{len(results)})")
        self.tabs.setCurrentIndex(idx)
        self.tabs.setVisible(True)
        self.statusBar().showMessage(
            f"Batch done: {ok} succeeded, {len(results) - ok} failed.", 8000)
        # Refresh the visible input preview with the first result's input.
        self.file_list.setCurrentRow(0)

    # ---- helpers ---------------------------------------------------------

    def _joined_notes(self, res: ConvertResult) -> str:
        parts = []
        if res.notes_in:
            parts.append("[input] " + res.notes_in.strip().replace("\n", " | "))
        if res.notes_out:
            parts.append("[output] " + res.notes_out.strip().replace("\n", " | "))
        return "\n".join(parts)

    @staticmethod
    def _fmt_bounds(b) -> str:
        if b is None:
            return "—"
        xmin, ymin, xmax, ymax = b
        return (f"{(xmax - xmin):.2f}×{(ymax - ymin):.2f} mm "
                f"(xmin={xmin:.2f} ymin={ymin:.2f} "
                f"xmax={xmax:.2f} ymax={ymax:.2f})")

    def _set_info(self, path: Path, geom: GeometrySet,
                  alignment: Optional[Alignment], notes: str) -> None:
        unit = (alignment.detected_unit.value if alignment and
                alignment.detected_unit else "—")
        note = (alignment.detection_note if alignment
                and alignment.detection_note else "—")
        counts = (f"lines={len(geom.lines)} arcs={len(geom.arcs)} "
                  f"circles={len(geom.circles)} polygons={len(geom.polygons)} "
                  f"paths={len(geom.paths)}")
        text = (f"<b>{path.name}</b>   format: {path.suffix.upper()[1:]}   "
               f"unit: <b>{unit}</b>   detection note: {note}<br>"
               f"bounding box: {self._fmt_bounds(geom.bounds())}<br>"
               f"{counts}")
        if notes.strip():
            text += (f"<br><span style='color:#888'>notes: "
                     f"{notes.strip().splitlines()[0]}</span>")
        self.info_lbl.setText(text)

    def _show_error(self, title: str, err: ConvertError,
                    captured: str = "") -> None:
        dlg = _ErrorDialog(title, str(err), parent=self)
        if captured:
            dlg._details.setPlainText(captured)
        dlg.exec()

    # ---- theme -----------------------------------------------------------

    def _toggle_theme(self) -> None:
        self._dark = not self._dark
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(theme.DARK if self._dark else theme.LIGHT)
        for c in (self.input_canvas, self.output_canvas):
            c.set_dark(self._dark)
        self.theme_btn.setText("☀ Light" if self._dark else "🌙 Dark")
