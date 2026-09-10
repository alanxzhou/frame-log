"""Native file/folder pickers, using the stdlib Tk dialogs in a helper process.

Tk is run in a separate interpreter so a hung or crashed dialog can't take the
server with it, and so the dialog gets its own main thread (Tk on macOS
insists on that). No third-party dependency: tkinter ships with python.org
and Windows Store builds; Homebrew python needs `brew install python-tk`.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Optional

_HELPER = r"""
import json, sys
try:
    import tkinter as tk
    from tkinter import filedialog
except ImportError:
    print(json.dumps({"error": "tkinter is not available in this Python; "
                      "install it (e.g. brew install python-tk) or type the path"}))
    sys.exit(0)
req = json.loads(sys.argv[1])
root = tk.Tk()
root.withdraw()
root.attributes("-topmost", True)   # in front of the browser window that asked for it
root.update()
kw = {"title": req.get("title") or "Select", "parent": root}
if req.get("initial"):
    kw["initialdir"] = req["initial"]
if req["kind"] == "folder":
    path = filedialog.askdirectory(mustexist=True, **kw)
else:
    kw["filetypes"] = [("Frame Log export", "*.json"), ("All files", "*.*")]
    path = filedialog.askopenfilename(**kw)
root.destroy()
print(json.dumps({"path": path or None}))
"""


def pick(kind: str, title: str = "", initial: Optional[str] = None, timeout: float = 600) -> dict:
    """Open a native picker. Returns {"path": str|None} or {"error": str}."""
    if kind not in ("folder", "file"):
        return {"error": f"unknown picker kind {kind!r}"}
    req = {"kind": kind, "title": title, "initial": initial if initial and Path(initial).is_dir() else None}
    try:
        res = subprocess.run([sys.executable, "-c", _HELPER, json.dumps(req)],
                             capture_output=True, text=True, encoding="utf-8", timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"error": "picker timed out"}
    lines = (res.stdout or "").strip().splitlines()
    if res.returncode != 0 or not lines:
        err_lines = (res.stderr or "").strip().splitlines()
        return {"error": err_lines[-1] if err_lines else "picker failed"}
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError:
        return {"error": "picker returned garbage"}
