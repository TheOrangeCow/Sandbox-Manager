"""
Windows Sandbox Manager - edit .wsb configs and launch sandboxes.
"""
import json
import os
import subprocess
import sys
import tempfile
import tkinter as tk
import xml.etree.ElementTree as ET
from tkinter import filedialog, messagebox, ttk

APP_NAME = "Windows Sandbox Manager"
TRI = ["Default", "Enable", "Disable"]
PREFS_PATH = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")),
                          "sandbox_manager_prefs.json")

DEFAULT_PREFS = {
    "theme": "vista" if sys.platform == "win32" else "clam",
    "confirm_launch": True,
    "default_dir": os.path.expanduser("~"),
    "default_sandbox_folder": r"C:\Users\WDAGUtilityAccount\Desktop",
    "live_preview": True,
    "warn_risky": True,
}


TRI_SETTINGS = [
    ("vGPU", "Virtual GPU (vGPU)",
     "Hardware-accelerated rendering. Disabling uses software rendering (WARP), "
     "which is slower but reduces attack surface."),
    ("Networking", "Networking",
     "Network access from inside the sandbox. Disabling isolates it completely."),
    ("AudioInput", "Audio input",
     "Shares the host microphone with the sandbox."),
    ("VideoInput", "Video input",
     "Shares the host webcam with the sandbox."),
    ("ProtectedClient", "Protected client",
     "Runs the sandbox RDP session with extra security (Windows 10 1903+ / 11)."),
    ("PrinterRedirection", "Printer redirection",
     "Makes host printers available inside the sandbox."),
    ("ClipboardRedirection", "Clipboard redirection",
     "Allows copy/paste between host and sandbox."),
]

PRESETS = {
    "Default (everything default)": {},
    "Locked down (malware analysis)": {
        "vGPU": "Disable", "Networking": "Disable", "AudioInput": "Disable",
        "VideoInput": "Disable", "ProtectedClient": "Enable",
        "PrinterRedirection": "Disable", "ClipboardRedirection": "Disable",
    },
    "Browsing / untrusted downloads": {
        "vGPU": "Enable", "Networking": "Enable", "AudioInput": "Disable",
        "VideoInput": "Disable", "ProtectedClient": "Enable",
        "PrinterRedirection": "Disable", "ClipboardRedirection": "Enable",
    },
    "Development / testing": {
        "vGPU": "Enable", "Networking": "Enable", "AudioInput": "Enable",
        "VideoInput": "Enable", "ProtectedClient": "Default",
        "PrinterRedirection": "Default", "ClipboardRedirection": "Enable",
        "MemoryInMB": 8192,
    },
}


def load_prefs():
    prefs = dict(DEFAULT_PREFS)
    try:
        with open(PREFS_PATH, "r", encoding="utf-8") as f:
            prefs.update(json.load(f))
    except Exception:
        pass
    return prefs


def save_prefs(prefs):
    try:
        with open(PREFS_PATH, "w", encoding="utf-8") as f:
            json.dump(prefs, f, indent=2)
    except Exception as e:
        messagebox.showwarning(APP_NAME, f"Could not save preferences:\n{e}")


class FolderDialog(tk.Toplevel):
    """Add/edit one mapped folder."""

    def __init__(self, parent, prefs, folder=None):
        super().__init__(parent)
        self.title("Mapped folder")
        self.resizable(False, False)
        self.transient(parent)
        self.result = None
        folder = folder or {"host": "", "sandbox": prefs["default_sandbox_folder"],
                            "readonly": True}
        self.prefs = prefs
        self.host = tk.StringVar(value=folder["host"])
        self.sandbox = tk.StringVar(value=folder["sandbox"])
        self.readonly = tk.BooleanVar(value=folder["readonly"])

        f = ttk.Frame(self, padding=12)
        f.grid()
        ttk.Label(f, text="Host folder:").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(f, textvariable=self.host, width=52).grid(row=0, column=1, padx=6)
        ttk.Button(f, text="Browse…", command=self.browse).grid(row=0, column=2)
        ttk.Label(f, text="Sandbox folder:").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(f, textvariable=self.sandbox, width=52).grid(row=1, column=1, padx=6)
        ttk.Label(f, text="(optional - blank maps to the sandbox Desktop)",
                  foreground="gray").grid(row=2, column=1, sticky="w")
        ttk.Checkbutton(f, text="Read-only (recommended)",
                        variable=self.readonly).grid(row=3, column=1, sticky="w", pady=6)
        btns = ttk.Frame(f)
        btns.grid(row=4, column=0, columnspan=3, sticky="e", pady=(8, 0))
        ttk.Button(btns, text="OK", command=self.ok).pack(side="left", padx=4)
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="left")
        self.bind("<Return>", lambda e: self.ok())
        self.bind("<Escape>", lambda e: self.destroy())
        self.grab_set()
        self.wait_window()

    def browse(self):
        d = filedialog.askdirectory(initialdir=self.prefs["default_dir"], parent=self)
        if d:
            self.host.set(os.path.normpath(d))

    def ok(self):
        host = self.host.get().strip()
        if not host:
            messagebox.showerror("Mapped folder", "Host folder is required.", parent=self)
            return
        if not os.path.isdir(host):
            if not messagebox.askyesno(
                    "Mapped folder",
                    "That host folder doesn't exist right now.\nUse it anyway?",
                    parent=self):
                return
        self.result = {"host": host, "sandbox": self.sandbox.get().strip(),
                       "readonly": self.readonly.get()}
        self.destroy()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.prefs = load_prefs()
        self.title(APP_NAME)
        self.geometry("820x640")
        self.minsize(720, 560)
        self.path = None
        self.dirty = False
        self.folders = []
        self.tri = {tag: tk.StringVar(value="Default") for tag, _, _ in TRI_SETTINGS}
        self.mem_on = tk.BooleanVar(value=False)
        self.mem = tk.IntVar(value=4096)
        self.status = tk.StringVar(value="Ready")
        self.warn = tk.StringVar()

        self.apply_theme()
        self.build_menu()
        self.build_ui()
        self.bind_vars()
        self.refresh()
        self.dirty = False
        self.update_title()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.bind("<Control-s>", lambda e: self.save())
        self.bind("<Control-o>", lambda e: self.open())
        self.bind("<Control-n>", lambda e: self.new())
        self.bind("<F5>", lambda e: self.launch())

    # UI
    def apply_theme(self):
        style = ttk.Style(self)
        theme = self.prefs["theme"]
        if theme in style.theme_names():
            style.theme_use(theme)

    def build_menu(self):
        m = tk.Menu(self)
        fm = tk.Menu(m, tearoff=0)
        fm.add_command(label="New", accelerator="Ctrl+N", command=self.new)
        fm.add_command(label="Open…", accelerator="Ctrl+O", command=self.open)
        fm.add_command(label="Save", accelerator="Ctrl+S", command=self.save)
        fm.add_command(label="Save As…", command=self.save_as)
        fm.add_separator()
        fm.add_command(label="Exit", command=self.on_close)
        m.add_cascade(label="File", menu=fm)

        sm = tk.Menu(m, tearoff=0)
        sm.add_command(label="Launch sandbox", accelerator="F5", command=self.launch)
        sm.add_command(label="Launch with default settings (no config)",
                       command=lambda: self.launch(blank=True))
        sm.add_separator()
        sm.add_command(label="Check if Windows Sandbox is installed",
                       command=self.check_installed)
        sm.add_command(label="Open Windows Features…",
                       command=lambda: self.run_detached(["optionalfeatures.exe"]))
        m.add_cascade(label="Sandbox", menu=sm)

        pm = tk.Menu(m, tearoff=0)
        for name in PRESETS:
            pm.add_command(label=name, command=lambda n=name: self.apply_preset(n))
        m.add_cascade(label="Presets", menu=pm)
        self.config(menu=m)

    def build_ui(self):
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=8, pady=(8, 0))
        self.build_general()
        self.build_folders()
        self.build_logon()
        self.build_preview()
        self.build_app_settings()

        bottom = ttk.Frame(self, padding=8)
        bottom.pack(fill="x")
        ttk.Label(bottom, textvariable=self.warn, foreground="#b45309",
                  wraplength=520).pack(side="left", fill="x", expand=True)
        ttk.Button(bottom, text="Save", command=self.save).pack(side="right", padx=3)
        ttk.Button(bottom, text="▶ Launch Sandbox",
                   command=self.launch).pack(side="right", padx=3)
        ttk.Label(self, textvariable=self.status, relief="sunken",
                  anchor="w").pack(fill="x", side="bottom")

    def build_general(self):
        tab = ttk.Frame(self.nb, padding=12)
        self.nb.add(tab, text="General")
        canvas_frame = ttk.LabelFrame(tab, text="Sandbox features", padding=10)
        canvas_frame.pack(fill="x")
        canvas_frame.columnconfigure(1, weight=1)
        for r, (tag, label, tip) in enumerate(TRI_SETTINGS):
            ttk.Label(canvas_frame, text=label).grid(row=r * 2, column=0, sticky="w",
                                                     pady=(6, 0))
            box = ttk.Frame(canvas_frame)
            box.grid(row=r * 2, column=1, sticky="w", padx=12, pady=(6, 0))
            for opt in TRI:
                ttk.Radiobutton(box, text=opt, value=opt,
                                variable=self.tri[tag]).pack(side="left", padx=4)
            ttk.Label(canvas_frame, text=tip, foreground="gray",
                      wraplength=640).grid(row=r * 2 + 1, column=0, columnspan=2,
                                           sticky="w")

        mf = ttk.LabelFrame(tab, text="Memory", padding=10)
        mf.pack(fill="x", pady=12)
        ttk.Checkbutton(mf, text="Set memory limit", variable=self.mem_on,
                        command=self.toggle_mem).grid(row=0, column=0, sticky="w")
        self.mem_spin = ttk.Spinbox(mf, from_=1024, to=262144, increment=256,
                                    textvariable=self.mem, width=10)
        self.mem_spin.grid(row=0, column=1, padx=8)
        ttk.Label(mf, text="MB").grid(row=0, column=2, sticky="w")
        self.mem_scale = ttk.Scale(mf, from_=1024, to=32768, orient="horizontal",
                                   command=self.on_scale, length=260)
        self.mem_scale.set(self.mem.get())
        self.mem_scale.grid(row=0, column=3, padx=12)
        ttk.Label(mf, text="Minimum 1024 MB. Leave unchecked for the system default.",
                  foreground="gray").grid(row=1, column=0, columnspan=4, sticky="w")
        self.toggle_mem()

    def build_folders(self):
        tab = ttk.Frame(self.nb, padding=12)
        self.nb.add(tab, text="Mapped Folders")
        ttk.Label(tab, text="Host folders shared into the sandbox.").pack(anchor="w")
        cols = ("host", "sandbox", "ro")
        self.tree = ttk.Treeview(tab, columns=cols, show="headings", height=10,
                                 selectmode="browse")
        self.tree.heading("host", text="Host folder")
        self.tree.heading("sandbox", text="Sandbox folder")
        self.tree.heading("ro", text="Read-only")
        self.tree.column("host", width=300)
        self.tree.column("sandbox", width=280)
        self.tree.column("ro", width=80, anchor="center")
        self.tree.pack(fill="both", expand=True, pady=8)
        self.tree.bind("<Double-1>", lambda e: self.edit_folder())
        btns = ttk.Frame(tab)
        btns.pack(anchor="w")
        ttk.Button(btns, text="Add…", command=self.add_folder).pack(side="left", padx=2)
        ttk.Button(btns, text="Edit…", command=self.edit_folder).pack(side="left", padx=2)
        ttk.Button(btns, text="Remove", command=self.remove_folder).pack(side="left", padx=2)
        ttk.Button(btns, text="Toggle read-only",
                   command=self.toggle_ro).pack(side="left", padx=2)

    def build_logon(self):
        tab = ttk.Frame(self.nb, padding=12)
        self.nb.add(tab, text="Logon Command")
        ttk.Label(tab, text="Command run automatically when the sandbox starts "
                            "(e.g. a script inside a mapped folder).\n"
                            "Example:  explorer.exe C:\\Users\\WDAGUtilityAccount\\Desktop\\Tools",
                  justify="left").pack(anchor="w")
        self.logon = tk.Text(tab, height=8, wrap="word", undo=True)
        self.logon.pack(fill="both", expand=True, pady=8)
        self.logon.bind("<<Modified>>", self.on_logon_modified)
        ttk.Button(tab, text="Clear", command=lambda: self.logon.delete("1.0", "end")
                   ).pack(anchor="w")

    def build_preview(self):
        tab = ttk.Frame(self.nb, padding=12)
        self.nb.add(tab, text="XML Preview")
        self.preview = tk.Text(tab, wrap="none", font=("Consolas", 10), state="disabled")
        ys = ttk.Scrollbar(tab, orient="vertical", command=self.preview.yview)
        self.preview.configure(yscrollcommand=ys.set)
        ys.pack(side="right", fill="y")
        self.preview.pack(fill="both", expand=True)

    def build_app_settings(self):
        tab = ttk.Frame(self.nb, padding=12)
        self.nb.add(tab, text="App Settings")
        f = ttk.LabelFrame(tab, text="This application", padding=10)
        f.pack(fill="x")
        f.columnconfigure(1, weight=1)

        self.p_theme = tk.StringVar(value=self.prefs["theme"])
        self.p_confirm = tk.BooleanVar(value=self.prefs["confirm_launch"])
        self.p_live = tk.BooleanVar(value=self.prefs["live_preview"])
        self.p_warn = tk.BooleanVar(value=self.prefs["warn_risky"])
        self.p_dir = tk.StringVar(value=self.prefs["default_dir"])
        self.p_sbf = tk.StringVar(value=self.prefs["default_sandbox_folder"])

        ttk.Label(f, text="Theme:").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Combobox(f, textvariable=self.p_theme, state="readonly",
                     values=sorted(ttk.Style(self).theme_names())
                     ).grid(row=0, column=1, sticky="w", padx=8)
        ttk.Label(f, text="Default browse folder:").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(f, textvariable=self.p_dir).grid(row=1, column=1, sticky="ew", padx=8)
        ttk.Button(f, text="Browse…", command=self.pick_default_dir).grid(row=1, column=2)
        ttk.Label(f, text="Default sandbox folder:").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(f, textvariable=self.p_sbf).grid(row=2, column=1, sticky="ew", padx=8)
        ttk.Checkbutton(f, text="Confirm before launching a sandbox",
                        variable=self.p_confirm).grid(row=3, column=0, columnspan=3, sticky="w")
        ttk.Checkbutton(f, text="Update XML preview live",
                        variable=self.p_live).grid(row=4, column=0, columnspan=3, sticky="w")
        ttk.Checkbutton(f, text="Show warnings for risky combinations",
                        variable=self.p_warn).grid(row=5, column=0, columnspan=3, sticky="w")
        ttk.Button(tab, text="Apply & save app settings",
                   command=self.apply_prefs).pack(anchor="w", pady=10)

    # state
    def bind_vars(self):
        for v in list(self.tri.values()) + [self.mem_on, self.mem]:
            v.trace_add("write", lambda *a: self.changed())

    def changed(self):
        self.dirty = True
        self.update_title()
        self.refresh()

    def toggle_mem(self):
        state = "normal" if self.mem_on.get() else "disabled"
        self.mem_spin.configure(state=state)
        self.mem_scale.state(["!disabled"] if self.mem_on.get() else ["disabled"])

    def on_scale(self, val):
        v = int(float(val) // 256 * 256)
        if self.mem_on.get() and v != self._safe_mem():
            self.mem.set(v)

    def _safe_mem(self):
        try:
            return int(self.mem.get())
        except (tk.TclError, ValueError):
            return 0

    def on_logon_modified(self, _e):
        if self.logon.edit_modified():
            self.logon.edit_modified(False)
            self.changed()

    def update_title(self):
        name = os.path.basename(self.path) if self.path else "Untitled"
        self.title(f"{'*' if self.dirty else ''}{name} - {APP_NAME}")

    # folders 
    def redraw_folders(self):
        self.tree.delete(*self.tree.get_children())
        for i, f in enumerate(self.folders):
            self.tree.insert("", "end", iid=str(i),
                             values=(f["host"], f["sandbox"] or "(Desktop)",
                                     "Yes" if f["readonly"] else "No"))
        self.changed()

    def selected_index(self):
        sel = self.tree.selection()
        return int(sel[0]) if sel else None

    def add_folder(self):
        r = FolderDialog(self, self.prefs).result
        if r:
            self.folders.append(r)
            self.redraw_folders()

    def edit_folder(self):
        i = self.selected_index()
        if i is None:
            return
        r = FolderDialog(self, self.prefs, self.folders[i]).result
        if r:
            self.folders[i] = r
            self.redraw_folders()

    def remove_folder(self):
        i = self.selected_index()
        if i is not None:
            del self.folders[i]
            self.redraw_folders()

    def toggle_ro(self):
        i = self.selected_index()
        if i is not None:
            self.folders[i]["readonly"] = not self.folders[i]["readonly"]
            self.redraw_folders()
            self.tree.selection_set(str(i))

    # XML 
    def build_xml(self):
        root = ET.Element("Configuration")
        for tag, _, _ in TRI_SETTINGS:
            v = self.tri[tag].get()
            if v != "Default":
                ET.SubElement(root, tag).text = v
        if self.mem_on.get():
            ET.SubElement(root, "MemoryInMB").text = str(max(1024, self._safe_mem()))
        if self.folders:
            mf = ET.SubElement(root, "MappedFolders")
            for f in self.folders:
                e = ET.SubElement(mf, "MappedFolder")
                ET.SubElement(e, "HostFolder").text = f["host"]
                if f["sandbox"]:
                    ET.SubElement(e, "SandboxFolder").text = f["sandbox"]
                ET.SubElement(e, "ReadOnly").text = "true" if f["readonly"] else "false"
        cmd = self.logon.get("1.0", "end").strip()
        if cmd:
            lc = ET.SubElement(root, "LogonCommand")
            ET.SubElement(lc, "Command").text = cmd
        ET.indent(root, space="  ") if hasattr(ET, "indent") else None
        return ET.tostring(root, encoding="unicode")

    def refresh(self):
        if not hasattr(self, "preview"):
            return
        if self.prefs["live_preview"]:
            self.preview.configure(state="normal")
            self.preview.delete("1.0", "end")
            self.preview.insert("1.0", self.build_xml())
            self.preview.configure(state="disabled")
        self.update_warnings()

    def update_warnings(self):
        msgs = []
        if self.prefs["warn_risky"]:
            net_on = self.tri["Networking"].get() != "Disable"
            rw = [f for f in self.folders if not f["readonly"]]
            if rw and net_on:
                msgs.append("Writable mapped folder + networking: malware could "
                            "modify host files or reach the network.")
            if self.tri["ClipboardRedirection"].get() == "Enable" and net_on:
                msgs.append("Clipboard is shared while networking is on.")
            if self.mem_on.get() and self._safe_mem() < 1024:
                msgs.append("Memory below 1024 MB will be raised to 1024.")
        self.warn.set("⚠ " + "  ⚠ ".join(msgs) if msgs else "")

    def load_xml(self, path):
        root = ET.parse(path).getroot()
        if root.tag != "Configuration":
            raise ValueError("Not a Windows Sandbox configuration file.")
        for v in self.tri.values():
            v.set("Default")
        self.mem_on.set(False)
        self.folders = []
        self.logon.delete("1.0", "end")
        for el in root:
            text = (el.text or "").strip()
            if el.tag in self.tri:
                self.tri[el.tag].set(text if text in TRI else "Default")
            elif el.tag == "MemoryInMB" and text.isdigit():
                self.mem_on.set(True)
                self.mem.set(int(text))
            elif el.tag == "MappedFolders":
                for mf in el.findall("MappedFolder"):
                    self.folders.append({
                        "host": (mf.findtext("HostFolder") or "").strip(),
                        "sandbox": (mf.findtext("SandboxFolder") or "").strip(),
                        "readonly": (mf.findtext("ReadOnly") or "true").strip().lower() == "true",
                    })
            elif el.tag == "LogonCommand":
                self.logon.insert("1.0", (el.findtext("Command") or "").strip())
        self.toggle_mem()
        self.mem_scale.set(self.mem.get())
        self.redraw_folders()

    # file ops 
    def confirm_discard(self):
        if not self.dirty:
            return True
        r = messagebox.askyesnocancel(APP_NAME, "Save changes first?")
        if r is None:
            return False
        if r:
            return self.save()
        return True

    def new(self):
        if not self.confirm_discard():
            return
        self.apply_preset("Default (everything default)")
        self.folders = []
        self.logon.delete("1.0", "end")
        self.redraw_folders()
        self.path = None
        self.dirty = False
        self.update_title()

    def open(self):
        if not self.confirm_discard():
            return
        p = filedialog.askopenfilename(
            initialdir=self.prefs["default_dir"],
            filetypes=[("Windows Sandbox config", "*.wsb"), ("All files", "*.*")])
        if not p:
            return
        try:
            self.load_xml(p)
        except Exception as e:
            messagebox.showerror(APP_NAME, f"Could not open file:\n{e}")
            return
        self.path = p
        self.dirty = False
        self.update_title()
        self.status.set(f"Opened {p}")

    def save(self):
        if not self.path:
            return self.save_as()
        return self.write(self.path)

    def save_as(self):
        p = filedialog.asksaveasfilename(
            initialdir=self.prefs["default_dir"], defaultextension=".wsb",
            filetypes=[("Windows Sandbox config", "*.wsb")])
        if not p:
            return False
        self.path = p
        return self.write(p)

    def write(self, p):
        try:
            with open(p, "w", encoding="utf-8") as f:
                f.write(self.build_xml())
        except Exception as e:
            messagebox.showerror(APP_NAME, f"Could not save:\n{e}")
            return False
        self.dirty = False
        self.update_title()
        self.status.set(f"Saved {p}")
        return True

    # sandbox
    def run_detached(self, cmd):
        try:
            subprocess.Popen(cmd, shell=False)
        except Exception as e:
            messagebox.showerror(APP_NAME, f"Could not run {cmd[0]}:\n{e}")

    def check_installed(self):
        exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                           "System32", "WindowsSandbox.exe")
        if sys.platform == "win32" and os.path.exists(exe):
            messagebox.showinfo(APP_NAME, "Windows Sandbox is installed.")
        else:
            messagebox.showwarning(
                APP_NAME,
                "Windows Sandbox was not found.\n\nEnable it under Windows Features "
                "('Windows Sandbox'), requires Pro/Enterprise/Education, virtualization "
                "enabled in BIOS, and a restart.")

    def launch(self, blank=False):
        if sys.platform != "win32":
            messagebox.showinfo(APP_NAME, "Windows Sandbox only runs on Windows. "
                                          "You can still edit and save configs here.")
            return
        if self.prefs["confirm_launch"] and not messagebox.askyesno(
                APP_NAME, "Launch Windows Sandbox now?\n(Only one instance can run at a time.)"):
            return
        try:
            if blank:
                subprocess.Popen(["WindowsSandbox.exe"])
            else:
                if self.path and not self.dirty:
                    target = self.path
                else:
                    fd, target = tempfile.mkstemp(suffix=".wsb", prefix="sandbox_")
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        f.write(self.build_xml())
                os.startfile(target)
            self.status.set("Sandbox launched")
        except Exception as e:
            messagebox.showerror(APP_NAME, f"Launch failed:\n{e}")

    # presets
    def apply_preset(self, name):
        p = PRESETS[name]
        for tag in self.tri:
            self.tri[tag].set(p.get(tag, "Default"))
        if "MemoryInMB" in p:
            self.mem_on.set(True)
            self.mem.set(p["MemoryInMB"])
        else:
            self.mem_on.set(False)
        self.toggle_mem()
        self.status.set(f"Preset applied: {name}")

    def pick_default_dir(self):
        d = filedialog.askdirectory(initialdir=self.p_dir.get())
        if d:
            self.p_dir.set(os.path.normpath(d))

    def apply_prefs(self):
        self.prefs.update({
            "theme": self.p_theme.get(),
            "confirm_launch": self.p_confirm.get(),
            "live_preview": self.p_live.get(),
            "warn_risky": self.p_warn.get(),
            "default_dir": self.p_dir.get(),
            "default_sandbox_folder": self.p_sbf.get(),
        })
        save_prefs(self.prefs)
        self.apply_theme()
        self.refresh()
        self.status.set("App settings saved")

    def on_close(self):
        if self.confirm_discard():
            self.destroy()


if __name__ == "__main__":
    App().mainloop()
