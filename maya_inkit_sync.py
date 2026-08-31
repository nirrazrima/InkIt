# maya_inkit_sync.py — Run in Maya Script Editor (Python). No restart needed.
import os
import socket
import threading

try:
    import maya.cmds as cmds
    import maya.utils as mutils
except:
    cmds = None
    mutils = None

if cmds is None:
    print("[InkIt] maya_inkit_sync.py must run inside Maya (maya.cmds was not importable).")
    raise SystemExit(0)

HOST = "127.0.0.1"
INKIT_PORT = 6005
MAYA_PORT = 6006

def _raw_send_to_inkit(msg):
    """Actual TCP send — runs only on the background sender thread."""
    try:
        s = socket.socket()
        s.settimeout(0.04)
        s.connect((HOST, INKIT_PORT))
        s.sendall((msg + "\n").encode())
        s.close()
        return True
    except:
        return False

# All outgoing messages flow through a single daemon thread so the Maya main
# thread never blocks on network I/O. A busy/just-started InkIt app used to
# stall connect() inside the 16ms frame-change events until the timeout and
# briefly freeze Maya every time that happened.
import collections
_inkit_out_queue = collections.deque()
_inkit_out_cond = threading.Condition()
if globals().get("_inkit_sender_started") is None:
    globals()["_inkit_sender_started"] = False

def _inkit_sender_worker():
    while True:
        with _inkit_out_cond:
            while not _inkit_out_queue:
                _inkit_out_cond.wait()
            msg = _inkit_out_queue.popleft()
            # coalesce: a burst of FRAME msgs during scrub/play — only the newest matters
            if msg.startswith("FRAME "):
                while _inkit_out_queue and _inkit_out_queue[0].startswith("FRAME "):
                    msg = _inkit_out_queue.popleft()
        if not _raw_send_to_inkit(msg) and not msg.startswith("FRAME "):
            try:
                print(f"[InkIt] Can't reach the InkIt app on port {INKIT_PORT} — is it running?")
            except:
                pass

def _send_to_inkit(msg):
    """Fire-and-forget: enqueue on the sender thread — never blocks the caller."""
    with _inkit_out_cond:
        _inkit_out_queue.append(msg)
        _inkit_out_cond.notify()
    if not globals().get("_inkit_sender_started", False):
        globals()["_inkit_sender_started"] = True
        try:
            t = threading.Thread(target=_inkit_sender_worker, daemon=True)
            t.start()
        except Exception:
            pass
    return True

def _run_main(fn):
    """Run a Maya-UI function on the main thread (server thread must never touch UI)."""
    try:
        mutils.executeInMainThreadWithResult(fn)
    except:
        try:
            mutils.executeDeferred(fn)
        except:
            pass

def _on_maya_time_changed(*_):
    if not globals().get("inkit_maya_sync_enabled", False):
        return
    if not globals().get("inkit_maya_to_inkit", True):
        return
    if globals().get("_maya_apply_guard", False):
        return
    try:
        f = int(float(cmds.currentTime(q=True)))
    except:
        return
    if f != globals().get("_maya_last_frame", None):
        globals()["_maya_last_frame"] = f
        _send_to_inkit(f"FRAME {f}")
        # the InkIt box is driven ONLY by the app's APPFRAME push (exact
        # number) — never recomputed here from offsets, or it would drift.

def _on_playing_back(*_):
    if not globals().get("inkit_maya_sync_enabled", False):
        return
    try:
        playing = bool(cmds.play(q=True, state=True))
    except:
        return
    _send_to_inkit("PLAY" if playing else "PAUSE")

def _maya_server_loop():
    srv = globals().get("inkit_maya_server")
    while globals().get("inkit_maya_sync_enabled", False):
        try:
            srv.settimeout(0.5)
            try:
                conn, _ = srv.accept()
            except socket.timeout:
                continue
            data = conn.recv(4096).decode(errors="ignore")
            conn.close()
            for line in data.splitlines():
                line=line.strip()
                if not line:
                    continue
                up = line.upper()
                if up.startswith("FRAME"):
                    if not globals().get("inkit_inkit_to_maya", True):
                        continue
                    try:
                        f = int(float(line.split()[1]))
                        globals()["_maya_apply_guard"] = True
                        def _set(f=f):
                            try:
                                cmds.currentTime(f, edit=True)
                                # this change came from InkIt — record it so the async
                                # timeChanged timer doesn't echo the frame straight back
                                globals()["_maya_last_frame"] = f
                            finally: globals()["_maya_apply_guard"] = False
                        try: mutils.executeInMainThreadWithResult(_set)
                        except: mutils.executeDeferred(_set)
                    except:
                        pass
                elif up.startswith("OFFSET"):
                    try:
                        off = int(float(line.split()[1]))
                        def _set_off(off=off):
                            try:
                                inkit_f = int(cmds.intField("inkit_inkitField", q=True, value=True))
                                cmds.intField("inkit_mayaField", edit=True, value=off + inkit_f)
                            except:
                                pass
                        _run_main(_set_off)
                    except:
                        pass
                elif up.startswith("SYNC"):
                    try:
                        on = bool(int(float(line.split()[1])))
                        globals()["inkit_maya_sync_enabled"] = on
                        _run_main(_update_status)
                        if on:
                            _start_maya_server()
                            _start_jobs()
                        else:
                            _stop_maya_server()
                            _stop_jobs()
                    except:
                        pass
                elif up.startswith("INKITFRAME"):
                    try:
                        f = int(float(line.split()[1]))
                        globals()["inkit_current_frame"] = f
                        # Only write the InkIt box when the user pressed Current Frame.
                        if not globals().get("_inkit_apply_after_frame", False):
                            continue
                        def _set_frame(f=f):
                            try:
                                globals()["_inkit_field_guard"] = True
                                try:
                                    cmds.intField("inkit_inkitField", edit=True, value=f)
                                finally:
                                    globals()["_inkit_field_guard"] = False
                                globals()["_inkit_apply_after_frame"] = False
                                _apply_link()
                            except:
                                pass
                        _run_main(_set_frame)
                    except:
                        pass
                elif up.startswith("APPFRAME"):
                    # Cache only — never drive the InkIt box from live scrub.
                    try:
                        globals()["inkit_current_frame"] = int(float(line.split()[1]))
                    except:
                        pass
                elif up == "PLAY":
                    try:
                        globals()["_maya_apply_guard"] = True
                        def _play():
                            try: cmds.play(forward=True)
                            finally: globals()["_maya_apply_guard"] = False
                        try: mutils.executeInMainThreadWithResult(_play)
                        except: mutils.executeDeferred(_play)
                    except:
                        pass
                elif up == "PAUSE":
                    try:
                        globals()["_maya_apply_guard"] = True
                        def _pause():
                            try: cmds.play(state=False)
                            finally: globals()["_maya_apply_guard"] = False
                        try: mutils.executeInMainThreadWithResult(_pause)
                        except: mutils.executeDeferred(_pause)
                    except:
                        pass
        except:
            break

def _start_maya_server():
    global inkit_maya_server
    if not globals().get("inkit_inkit_to_maya", True):
        return True
    if globals().get("inkit_maya_server"):
        return True
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind((HOST, MAYA_PORT))
        s.listen(5)
    except Exception as e:
        try: cmds.warning(f"[InkIt] Can't listen on {HOST}:{MAYA_PORT}: {e}")
        except: pass
        return False
    globals()["inkit_maya_server"] = s
    t = threading.Thread(target=_maya_server_loop, daemon=True)
    t.start()
    return True

def _stop_maya_server():
    s = globals().get("inkit_maya_server")
    if s:
        try: s.close()
        except: pass
    globals()["inkit_maya_server"] = None

def _kill_job(name):
    try:
        if name in globals():
            j = globals()[name]
            if j is not None and cmds.scriptJob(exists=j):
                cmds.scriptJob(kill=j, force=True)
    except:
        pass

def _start_jobs():
    global inkit_maya_job, inkit_maya_timer, inkit_maya_play_job
    _kill_job("inkit_maya_job")
    _kill_job("inkit_maya_timer")
    _kill_job("inkit_maya_play_job")
    if not globals().get("inkit_maya_to_inkit", True):
        return
    try:
        inkit_maya_job = cmds.scriptJob(attributeChange=["time1.outTime", _on_maya_time_changed])
    except:
        inkit_maya_job = cmds.scriptJob(event=["timeChanged", _on_maya_time_changed])
    try:
        from PySide6.QtCore import QTimer
    except:
        try:
            from PySide2.QtCore import QTimer
        except:
            QTimer = None
    if QTimer:
        t = QTimer()
        t.setInterval(16)
        t.timeout.connect(_on_maya_time_changed)
        t.start()
        globals()["inkit_maya_timer"] = t
    try:
        inkit_maya_play_job = cmds.scriptJob(conditionChange=["playingBack", _on_playing_back])
    except:
        pass

def _stop_jobs():
    _kill_job("inkit_maya_job")
    _kill_job("inkit_maya_play_job")
    t = globals().get("inkit_maya_timer")
    if t:
        try:
            t.stop()
        except:
            try: cmds.scriptJob(kill=t, force=True)
            except: pass
        globals()["inkit_maya_timer"] = None

def _update_status(*_):
    _update_status_bar()

def _patch_button(name, css):
    """Apply QSS to a Maya QPushButton (works on Maya 2022“2027 by falling back
    across PySide2/shiboken2 and PySide6/shiboken6)."""
    try:
        import maya.OpenMayaUI as omui
        path = cmds.control(name, q=True, path=True)
        if not path:
            return
        ptr = omui.MQtUtil.findControl(path)
        if not ptr:
            return
        for qtw_name, shib_name in (("PySide2", "shiboken2"), ("PySide6", "shiboken6")):
            try:
                shib = __import__(shib_name, fromlist=["wrapInstance"])
                qtw = __import__(qtw_name, fromlist=["QtWidgets"])
                shib.wrapInstance(int(ptr), qtw.QtWidgets.QPushButton).setStyleSheet(css)
                return
            except Exception:
                continue
    except Exception:
        pass

def _style_sync_button(on):
    """Flat green rectangle as in reference image — no pill rounding."""
    css = (
        "QPushButton{border-radius:2px;background:%(bg)s;color:%(fg)s;"
        "font-weight:bold;padding:4px 10px;border:none;}"
        "QPushButton:hover{background:%(hover)s;}"
    ) % {
        "bg": "#2e9a44" if on else "#5a5a5a",
        "hover": "#35b24f" if on else "#646464",
        "fg": "#0a0a0a" if on else "#e8e8e8",
    }
    _patch_button("inkit_syncBtn", css)

def _refresh_sync_button(on):
    try:
        cmds.button("inkit_syncBtn", edit=True,
                    label="● SYNC ON" if on else "○ SYNC OFF",
                    backgroundColor=[0.18, 0.60, 0.27] if on else [0.32, 0.32, 0.32])
        _style_sync_button(on)
    except:
        pass

_WIN_KEY_W = "inkitSyncWindowW"
_WIN_KEY_H = "inkitSyncWindowH"
_WIN_DEFAULT = (368, 210)

def _inkit_window_size():
    try:
        if cmds.optionVar(exists=_WIN_KEY_W):
            w = cmds.optionVar(q=_WIN_KEY_W)
        else:
            w = _WIN_DEFAULT[0]
        if cmds.optionVar(exists=_WIN_KEY_H):
            h = cmds.optionVar(q=_WIN_KEY_H)
        else:
            h = _WIN_DEFAULT[1]
        return (max(280, int(w)), max(150, int(h)))
    except:
        return _WIN_DEFAULT

def _save_window_size(*_):
    try:
        w, h = cmds.window("inkitSyncWindow", q=True, widthHeight=True)
        w, h = int(w), int(h)
        if w >= 280 and h >= 150:
            cmds.optionVar(iv=[_WIN_KEY_W, w, _WIN_KEY_H, h])
    except:
        pass

def _apply_link(*a):
    """Explicit Apply button: connect the pair typed in the boxes.
    Maya box = timeline frame, InkIt box = app frame. Offset = maya − inkit,
    so the app lands on EXACTLY the InkIt number and Maya lands on its box."""
    if globals().get("_inkit_field_guard", False):
        return
    try:
        maya_v = int(cmds.intField("inkit_mayaField", q=True, value=True))
        inkit_v = int(cmds.intField("inkit_inkitField", q=True, value=True))
    except Exception:
        return
    globals()["_inkit_offset"] = maya_v - inkit_v
    _send_to_inkit(f"OFFSET {maya_v - inkit_v}")
    _send_to_inkit(f"FRAME {maya_v}")  # app -> maya_v - offset == inkit_v exactly
    if globals().get("inkit_inkit_to_maya", True):
        try:
            globals()["_maya_apply_guard"] = True
            try:
                cmds.currentTime(maya_v)
            finally:
                globals()["_maya_apply_guard"] = False
        except:
            pass
    _update_status_bar()

def _update_status_bar(*_):
    """Small status bar: live dot + the exact Maya/InkIt pair being connected."""
    try:
        on = bool(globals().get("inkit_maya_sync_enabled", False))
        dot = "● Live" if on else "○ Off"
        mv, iv = 0, 0
        try:
            mv = int(cmds.intField("inkit_mayaField", q=True, value=True))
        except:
            pass
        try:
            iv = int(cmds.intField("inkit_inkitField", q=True, value=True))
        except:
            pass
        cmds.text("inkit_statusTxt", edit=True, label=f"{dot}   Maya {mv} = InkIt {iv}")
        _refresh_sync_button(on)
    except:
        pass

def _refresh_inkit_box_from_app(*_):
    """Ask the app for its exact frame; the INKITFRAME reply rewrites the box.
    Returns True if the app answered (reachable)."""
    if globals().get("_inkit_field_guard", False):
        return False
    return _send_to_inkit("CURFRAME?")

def _go_drawing(msg):
    if _send_to_inkit(msg):
        print(f"[InkIt] Sent {msg} to app")
        # ask the app right back for its current frame so the box/status bar
        # show whether it actually jumped
        try:
            t = threading.Timer(0.15, _refresh_inkit_box_from_app)
            t.daemon = True
            t.start()
        except:
            _refresh_inkit_box_from_app()
    else:
        try:
            cmds.warning("[InkIt] Can't reach the InkIt app on port 6005 — is it running?")
        except:
            pass

def _use_current(*_):
    """Maya Current Frame: fill the Maya box from the timeline and apply the pair."""
    try:
        f = int(float(cmds.currentTime(q=True)))
        globals()["_inkit_field_guard"] = True
        try:
            cmds.intField("inkit_mayaField", edit=True, value=f)
        finally:
            globals()["_inkit_field_guard"] = False
        _apply_link()
    except:
        pass

def _sync_inkit_frame(*_):
    """InkIt Current Frame: fill the InkIt box from the app and apply the pair."""
    globals()["_inkit_apply_after_frame"] = True
    try:
        cached = globals().get("inkit_current_frame", None)
        if isinstance(cached, int):
            globals()["_inkit_field_guard"] = True
            try:
                cmds.intField("inkit_inkitField", edit=True, value=cached)
            finally:
                globals()["_inkit_field_guard"] = False
            _apply_link()
    except:
        pass
    if _refresh_inkit_box_from_app():
        try:
            cmds.text("inkit_statusTxt", edit=True, label="Refreshing InkIt current frame…")
        except:
            pass
    else:
        try:
            cmds.text("inkit_statusTxt", edit=True, label="○ Off  —  InkIt app not reachable")
        except:
            pass
        globals()["_inkit_apply_after_frame"] = False
        _update_status_bar()

def _toggle_sync(*_):
    on = not bool(globals().get("inkit_maya_sync_enabled", False))
    globals()["inkit_maya_sync_enabled"] = on
    _refresh_sync_button(on)
    _send_to_inkit(f"SYNC {int(on)}")
    if on:
        _start_maya_server()
        _start_jobs()
        _update_status_bar()
        _on_maya_time_changed()
    else:
        _kill_job("inkit_maya_job")
        t = globals().get("inkit_maya_timer")
        if t:
            try: t.stop()
            except:
                try: cmds.scriptJob(kill=t, force=True)
                except: pass
            globals()["inkit_maya_timer"] = None
        _kill_job("inkit_maya_play_job")
        _stop_maya_server()
        _update_status_bar()

def _toggle_dir(*_):
    maya_to_inkit = bool(cmds.checkBox("inkit_dirMayaToInkit", q=True, value=True))
    inkit_to_maya = bool(cmds.checkBox("inkit_dirInkitToMaya", q=True, value=True))
    globals()["inkit_maya_to_inkit"] = maya_to_inkit
    globals()["inkit_inkit_to_maya"] = inkit_to_maya
    if globals().get("inkit_maya_sync_enabled", False):
        if inkit_to_maya:
            _start_maya_server()
        else:
            _stop_maya_server()
        # restart maya->inkit jobs/timer to respect new direction
        _kill_job("inkit_maya_job")
        t = globals().get("inkit_maya_timer")
        if t:
            try: t.stop()
            except: pass
            globals()["inkit_maya_timer"] = None
        if maya_to_inkit:
            try:
                global inkit_maya_job
                inkit_maya_job = cmds.scriptJob(attributeChange=["time1.outTime", _on_maya_time_changed])
            except:
                inkit_maya_job = cmds.scriptJob(event=["timeChanged", _on_maya_time_changed])
            try:
                from PySide6.QtCore import QTimer
            except:
                try:
                    from PySide2.QtCore import QTimer
                except:
                    QTimer = None
            if QTimer:
                tt = QTimer()
                tt.setInterval(16)
                tt.timeout.connect(_on_maya_time_changed)
                tt.start()
                globals()["inkit_maya_timer"] = tt

def _close_window(*_):
    _save_window_size()
    _send_to_inkit("SYNC 0")
    _kill_job("inkit_maya_job")
    t = globals().get("inkit_maya_timer")
    if t:
        try: t.stop()
        except:
            try: cmds.scriptJob(kill=t, force=True)
            except: pass
    _kill_job("inkit_maya_play_job")
    _stop_maya_server()
    try: cmds.deleteUI("inkitSyncWindow")
    except: pass

# cleanup old window
try:
    if cmds.window("inkitSyncWindow", exists=True):
        _close_window()
except:
    pass

INKIT_BTN_CMD = ("import sys,os;d=os.path.join(os.path.expanduser('~'),"
                 "'Documents','maya','scripts');sys.path.insert(0,d);"
                 "_x=sys.modules.pop('maya_inkit_sync',None);"
                 "import maya_inkit_sync")

# Icon shipped beside this script by the InkIt app — absolute path so it works
# for any Maya version and mid-session, no prefs/icons lookup needed.
try:
    _INKIT_ICON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "InkIt.png").replace("\\", "/")
    if not os.path.isfile(_INKIT_ICON):
        raise ValueError("icon not deployed yet")
except Exception:
    _INKIT_ICON = "InkIt"  # fall back to the name-based prefs/icons lookup

# Previous/next drawing icons — same artwork the InkIt app uses, so the Maya
# window buttons match the app. Looked up from the app's icons folder (or a
# sibling 'icons' folder next to this script).
def _find_draw_icon(name):
    cands = []
    try:
        cands.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons"))
    except:
        pass
    cands.append(r"D:\Apps\InkIt\icons")
    cands.append(os.path.join(os.path.expanduser("~"), "Desktop", "InkIt", "icons"))
    for c in cands:
        p = os.path.join(c, name)
        if os.path.isfile(p):
            return p.replace("\\", "/")
    return None

_DRAW_ICON_PREV = _find_draw_icon("nav_prev_dot.png")
_DRAW_ICON_NEXT = _find_draw_icon("nav_next_dot.png")

def _ensure_active_shelf_button():
    """Adds the InkIt Sync button to the currently selected/visible (active)
    shelf only, and heals a stale one if present."""
    try:
        try:
            target = cmds.shelfTabLayout("ShelfLayout", q=True, selectTab=True)
        except:
            target = None
        if not target:
            return
        existing = None
        for k in (cmds.shelfLayout(target, q=True, childArray=True) or []):
            try:
                if str(cmds.shelfButton(k, q=True, annotation=True) or "") == "InkIt Sync":
                    existing = k
                    break
            except:
                pass
        if existing is not None:
            # heal stale command/icon/label/size
            needs_fix = False
            try:
                cur = str(cmds.shelfButton(existing, q=True, command=True) or "")
            except Exception:
                cur = ""
            if INKIT_BTN_CMD not in cur:
                needs_fix = True
            try:
                img = str(cmds.shelfButton(existing, q=True, image=True) or "")
            except Exception:
                img = ""
            if img != _INKIT_ICON and os.path.isabs(_INKIT_ICON):
                needs_fix = True
            try:
                lbl = str(cmds.shelfButton(existing, q=True, imageOverlayLabel=True) or "")
            except Exception:
                lbl = "InkIt"  # can't query — clear it to be safe
            if lbl:
                needs_fix = True
            try:
                w = int(cmds.shelfButton(existing, q=True, width=True) or 0)
                h = int(cmds.shelfButton(existing, q=True, height=True) or 0)
            except Exception:
                w = h = 0
            if w != 35 or h != 35:
                needs_fix = True
            if needs_fix:
                cmds.shelfButton(
                    existing, edit=True,
                    width=35, height=35,
                    sourceType="python",
                    command=INKIT_BTN_CMD,
                    image1=_INKIT_ICON,
                    imageOverlayLabel="",
                )
        else:
            cmds.setParent(target)
            cmds.shelfButton(
                annotation="InkIt Sync",
                width=35,
                height=35,
                image1=_INKIT_ICON,
                sourceType="python",
                command=INKIT_BTN_CMD,
            )
            print("[InkIt] Shelf button added to active shelf: %s" % target)
    except:
        pass

globals().setdefault("_maya_apply_guard", False)
globals().setdefault("_maya_last_frame", None)
globals().setdefault("inkit_maya_to_inkit", True)
globals().setdefault("inkit_inkit_to_maya", True)
globals().setdefault("inkit_current_frame", None)
globals().setdefault("_inkit_offset", 0)
globals().setdefault("_inkit_field_guard", False)
globals().setdefault("_inkit_apply_after_frame", False)

# ——— UI ———
# silent install mode (INKIT_SILENT_INSTALL=1): only places the shelf button
# live via _ensure_active_shelf_button(); no window, no sync, no servers.
_inkit_silent_install = os.environ.get("INKIT_SILENT_INSTALL") == "1"
if not _inkit_silent_install:
    win = cmds.window("inkitSyncWindow", title="InkIt Sync", sizeable=True,
                      resizeToFitChildren=False,
                      widthHeight=_inkit_window_size(),
                      closeCommand=_save_window_size)
    cmds.columnLayout(adjustableColumn=True, rowSpacing=7, columnAttach=("both", 10))

    # Maya — Current Frame fills the Maya box from the timeline and applies.
    cmds.rowLayout("inkit_mayaRow", numberOfColumns=4, columnWidth4=(62, 112, 50, 10), adjustableColumn=4, columnAttach=[(1, "left", 0), (2, "left", 6), (3, "left", 6)])
    cmds.text("inkit_mayaLabel", label="Maya", font="boldLabelFont", width=58, align="left")
    cmds.button("inkit_mayaBtn", label="Current Frame", width=108, height=22, backgroundColor=[0.43, 0.43, 0.43], command=_use_current)
    cmds.intField("inkit_mayaField", value=int(float(cmds.currentTime(q=True))), width=50, height=22, preventOverride=True, changeCommand=_apply_link)
    cmds.setParent("..")

    # Inkit — Current Frame pulls the app's live frame and applies the pair.
    cmds.rowLayout("inkit_inkitRow", numberOfColumns=4, columnWidth4=(62, 112, 50, 10), adjustableColumn=4, columnAttach=[(1, "left", 0), (2, "left", 6), (3, "left", 6)])
    cmds.text("inkit_inkitLabel", label="Inkit", font="boldLabelFont", width=58, align="left")
    cmds.button("inkit_inkitBtn", label="Current Frame", width=108, height=22, backgroundColor=[0.43, 0.43, 0.43], command=_sync_inkit_frame)
    cmds.intField("inkit_inkitField", value=0, width=50, height=22, preventOverride=True, changeCommand=_apply_link)
    cmds.setParent("..")

    if _DRAW_ICON_PREV and _DRAW_ICON_NEXT:
        cmds.rowLayout("inkit_drawRow", numberOfColumns=3, adjustableColumn=3, columnAttach=[(1, "left", 0), (2, "left", 4)])
        cmds.symbolButton("inkit_prevDrawBtn", image=_DRAW_ICON_PREV, width=30, height=30, annotation="Jump to previous drawing", command=lambda *_: _go_drawing("PREVDRAW"))
        cmds.symbolButton("inkit_nextDrawBtn", image=_DRAW_ICON_NEXT, width=30, height=30, annotation="Jump to next drawing", command=lambda *_: _go_drawing("NEXTDRAW"))
        cmds.setParent("..")
    else:
        cmds.rowLayout("inkit_drawRow", numberOfColumns=3, columnWidth3=(150, 160, 10), adjustableColumn=3, columnAttach=[(1, "left", 0), (2, "left", 4)])
        cmds.button("inkit_prevDrawBtn", label="◀ Previous Drawing", width=146, height=22, backgroundColor=[0.33, 0.40, 0.52], command=lambda *_: _go_drawing("PREVDRAW"))
        cmds.button("inkit_nextDrawBtn", label="Next Drawing ▶", width=156, height=22, backgroundColor=[0.33, 0.40, 0.52], command=lambda *_: _go_drawing("NEXTDRAW"))
        cmds.setParent("..")

    cmds.separator(height=4, style="single")

    # bottom: checkboxes left (stacked) + SYNC button right — as in reference
    cmds.rowLayout("inkit_bottomRow", numberOfColumns=3, columnWidth3=(150, 104, 10), adjustableColumn=3, columnAttach=[(1, "left", 6), (2, "right", 6)])
    cmds.columnLayout(adjustableColumn=True, rowSpacing=6)
    cmds.checkBox("inkit_dirMayaToInkit", label="Maya → Inkit", value=True, changeCommand=_toggle_dir)
    cmds.checkBox("inkit_dirInkitToMaya", label="Inkit → Maya", value=True, changeCommand=_toggle_dir)
    cmds.setParent("..")
    cmds.button("inkit_syncBtn", label="● SYNC ON", width=96, height=28, backgroundColor=[0.18, 0.60, 0.27], command=_toggle_sync)
    cmds.setParent("..")

    # small status bar: which frame numbers are being connected
    cmds.rowLayout("inkit_statusRow", numberOfColumns=1, columnAttach=[(1, "both", 10)])
    cmds.text("inkit_statusTxt", label="● Live   Maya 0 = InkIt 0", height=18, align="left")
    cmds.setParent("..")
    try:
        cmds.control("inkit_statusRow", edit=True, visible=True)
        cmds.control("inkit_statusTxt", edit=True, visible=True)
    except:
        pass

# place/refresh the button on whatever shelf tab is currently active
_ensure_active_shelf_button()

if not _inkit_silent_install:
    cmds.showWindow(win)

if not _inkit_silent_install:
    globals()["inkit_maya_sync_enabled"] = True
    _refresh_sync_button(True)
    _start_maya_server()
    # tell InkIt the sync was initiated from here — the app only reflects this
    _send_to_inkit("SYNC 1")
    _kill_job("inkit_maya_job")
    try:
        inkit_maya_job = cmds.scriptJob(attributeChange=["time1.outTime", _on_maya_time_changed])
    except:
        inkit_maya_job = cmds.scriptJob(event=["timeChanged", _on_maya_time_changed])
    try:
        from PySide6.QtCore import QTimer
        t = QTimer(); t.setInterval(16); t.timeout.connect(_on_maya_time_changed); t.start(); globals()["inkit_maya_timer"] = t
    except:
        try:
            from PySide2.QtCore import QTimer
            t = QTimer(); t.setInterval(16); t.timeout.connect(_on_maya_time_changed); t.start(); globals()["inkit_maya_timer"] = t
        except:
            globals()["inkit_maya_timer"] = None
    try:
        inkit_maya_play_job = cmds.scriptJob(conditionChange=["playingBack", _on_playing_back])
    except:
        pass
    # ensure Maya commandPort is open so InkIt can wake us when window is closed
    try:
        if not cmds.commandPort(":7002", q=True):
            cmds.commandPort(name=":7002", sourceType="python", echoOutput=False)
    except:
        pass
    print(f"[InkIt] Ready — Maya:{MAYA_PORT} <-> InkIt:{INKIT_PORT} (drag = live)")
    cmds.scriptJob(uiDeleted=["inkitSyncWindow", _close_window])
