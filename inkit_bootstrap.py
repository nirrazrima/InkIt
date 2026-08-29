# inkit_bootstrap.py — loaded by Maya (userSetup / InkIt inject). Safe no-op outside Maya.
"""Open commandPort and put the InkIt button on the currently selected shelf."""
from __future__ import annotations

import os
import tempfile

HOST_PORT = ":7002"


def _cmds():
    try:
        import maya.cmds as cmds
        return cmds
    except Exception:
        return None


def ensure_port() -> bool:
    cmds = _cmds()
    if not cmds:
        return False
    try:
        if not cmds.commandPort(HOST_PORT, q=True):
            cmds.commandPort(name=HOST_PORT, sourceType="python", echoOutput=False)
        return bool(cmds.commandPort(HOST_PORT, q=True))
    except Exception:
        try:
            cmds.commandPort(name=HOST_PORT, sourceType="python", echoOutput=False)
            return True
        except Exception:
            return False


def get_active_shelf() -> str:
    cmds = _cmds()
    if not cmds:
        return ""
    try:
        target = cmds.shelfTabLayout("ShelfLayout", q=True, selectTab=True)
        return str(target) if target else ""
    except Exception:
        return ""


def check_shelf_button(shelf: str | None = None) -> tuple[bool, str]:
    """Check if the InkIt Sync shelf button exists on the specified or active shelf.
    Returns (exists, shelf_name)."""
    cmds = _cmds()
    if not cmds:
        return False, ""
    target = shelf or get_active_shelf()
    if not target:
        return False, ""
    try:
        kids = cmds.shelfLayout(target, q=True, childArray=True) or []
    except Exception:
        kids = []
    for k in kids:
        try:
            if str(cmds.shelfButton(k, q=True, annotation=True) or "") == "InkIt Sync":
                return True, target
        except Exception:
            pass
    return False, target


def install_shelf() -> tuple[bool, str, str]:
    """Check if button exists on active shelf. If not exist, create it.
    Returns (success, action, shelf_name) where action is 'exists' or 'created'."""
    cmds = _cmds()
    if not cmds:
        return False, "no_cmds", ""
    target = get_active_shelf()
    if not target:
        return False, "no_shelf", ""

    scripts = os.path.join(os.path.expanduser("~"), "Documents", "maya", "scripts")
    icon = os.path.join(scripts, "InkIt.png").replace("\\", "/")
    if not os.path.isfile(icon):
        icon = "InkIt"

    cmd = (
        "import sys,os;d=os.path.join(os.path.expanduser('~'),"
        "'Documents','maya','scripts');sys.path.insert(0,d);"
        "_x=sys.modules.pop('maya_inkit_sync',None);"
        "import maya_inkit_sync"
    )

    exists, _ = check_shelf_button(target)

    if exists:
        try:
            kids = cmds.shelfLayout(target, q=True, childArray=True) or []
            for k in kids:
                if str(cmds.shelfButton(k, q=True, annotation=True) or "") == "InkIt Sync":
                    cmds.shelfButton(
                        k, edit=True, width=35, height=35, image1=icon,
                        sourceType="python", command=cmd, imageOverlayLabel="",
                    )
                    break
            return True, "exists", target
        except Exception:
            return True, "exists", target
    else:
        try:
            cmds.setParent(target)
            cmds.shelfButton(
                annotation="InkIt Sync", width=35, height=35,
                image1=icon, sourceType="python", command=cmd,
                imageOverlayLabel="",
            )
            return True, "created", target
        except Exception as e:
            return False, str(e), target


def _write_answer(ok: bool, action: str = "", shelf: str = "") -> None:
    path = os.path.join(tempfile.gettempdir(), "inkit_shelf_install.txt")
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(f"{'1' if ok else '0'} {action} {shelf}".strip())
    except Exception:
        pass


def run() -> str:
    """Called from InkIt (inject or commandPort) and from Maya userSetup."""
    ensure_port()
    ok, action, shelf = install_shelf()
    _write_answer(ok, action, shelf)
    return shelf
