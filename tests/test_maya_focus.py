"""Headless tests for the Maya panel focus guard in maya_inkit_sync.py.
Extracts the pure-Qt block between the guard markers and runs it against a
fake 'Maya main window' (no Maya needed)."""
import os, sys, pathlib
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6 import QtCore, QtWidgets, QtGui
from PySide6.QtTest import QTest

SRC = (pathlib.Path(__file__).resolve().parents[1] / "maya_inkit_sync.py").read_text(encoding="utf-8")
block = SRC.split("# --- InkIt focus guard begin ---")[1].split("# --- InkIt focus guard end ---")[0]
ns = {}
exec(block, ns)

fails = 0
def check(c, m):
    global fails
    print(("ok   " if c else "FAIL: ") + m)
    fails += 0 if c else 1

class FakeMaya(QtWidgets.QWidget):
    def __init__(self):
        super().__init__(); self.keys = []; self.setFocusPolicy(QtCore.Qt.StrongFocus)
    def keyPressEvent(self, e): self.keys.append(e.key())

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
check(ns["_inkit_qt"]() is not None, "Qt binding found (PySide6/PySide2)")
maya = FakeMaya(); maya.resize(400, 300); maya.show()
panel = QtWidgets.QWidget(maya, QtCore.Qt.Tool)
lay = QtWidgets.QVBoxLayout(panel)
field = QtWidgets.QLineEdit("1"); spin = QtWidgets.QSpinBox(); btn = QtWidgets.QPushButton("Apply")
for w in (field, spin, btn): lay.addWidget(w)
committed = []
field.returnPressed.connect(lambda: committed.append(field.text()))
Guard = ns["_inkit_make_focus_guard"](QtCore, QtWidgets, QtGui, lambda: maya)
g = Guard(panel)
panel.show(); app.processEvents()

check(field.focusPolicy() == QtCore.Qt.ClickFocus and spin.focusPolicy() == QtCore.Qt.ClickFocus, "text fields are ClickFocus")
check(btn.focusPolicy() == QtCore.Qt.NoFocus and panel.focusPolicy() == QtCore.Qt.NoFocus, "buttons/container are NoFocus")
check(panel.testAttribute(QtCore.Qt.WA_ShowWithoutActivating), "panel shows without activating")
check(not field.hasFocus() and not spin.hasFocus(), "no field focused on open")

# hotkey while panel selected but nothing edited -> Maya
QTest.keyClick(panel, QtCore.Qt.Key_S); app.processEvents()
check(QtCore.Qt.Key_S in maya.keys, "unhandled key on panel goes to Maya")
maya.keys.clear()
QTest.keyClick(btn, QtCore.Qt.Key_W); app.processEvents()
check(QtCore.Qt.Key_W in maya.keys, "key on a button goes to Maya")

# click into the field -> typing stays in the field
QTest.mouseClick(field, QtCore.Qt.LeftButton); app.processEvents()
panel.activateWindow(); field.setFocus(QtCore.Qt.MouseFocusReason); app.processEvents()
maya.keys.clear()
check(field.hasFocus(), "clicking a field focuses it"); field.selectAll(); QTest.keyClicks(field, "42"); app.processEvents()
check(field.text() == "42" and not maya.keys, "typing in a clicked field stays in the field")
QTest.keyClick(field, QtCore.Qt.Key_Return); app.processEvents(); QTest.qWait(10); app.processEvents()
check(committed == ["42"] and not field.hasFocus(), "Enter commits then releases focus")
check(QtWidgets.QApplication.focusWidget() is maya, "focus handed back to Maya after Enter")

panel.activateWindow(); field.setFocus(QtCore.Qt.MouseFocusReason); app.processEvents()
QTest.keyClick(field, QtCore.Qt.Key_Escape); app.processEvents()
check(not field.hasFocus() and QtWidgets.QApplication.focusWidget() is maya, "Escape releases focus to Maya")

# spin box inner line edit counts as a field
panel.activateWindow(); spin.setFocus(QtCore.Qt.MouseFocusReason); app.processEvents()
maya.keys.clear(); QTest.keyClicks(spin.findChild(QtWidgets.QLineEdit), "7"); app.processEvents()
check(not maya.keys, "typing in a spin box stays in it")
g.clear_field_focus()

# button click hands focus back
QTest.mouseClick(btn, QtCore.Qt.LeftButton); QTest.qWait(10); app.processEvents()
check(QtWidgets.QApplication.focusWidget() is maya, "button click returns focus to Maya")

# widgets added later (cmds rebuilds) are guarded too
late = QtWidgets.QLineEdit(); lay.addWidget(late); QTest.qWait(10); app.processEvents()
check(late.focusPolicy() == QtCore.Qt.ClickFocus, "fields added later get ClickFocus")

print("ALL MAYA FOCUS CHECKS PASSED" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
