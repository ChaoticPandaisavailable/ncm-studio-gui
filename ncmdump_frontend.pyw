# -*- coding: utf-8 -*-
import ctypes
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from tkinter import BOTH, END, LEFT, RIGHT, W, X, BooleanVar, Button, Canvas, Label, StringVar, Tk, Toplevel, filedialog, messagebox
from tkinter import Listbox, scrolledtext
from tkinter import font as tkfont
from tkinter import ttk


ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
AUDIO_EXTENSIONS = (".mp3", ".flac")
CONFIG_FILENAME = "ncmdump_gui_state.json"


def enable_high_dpi():
    if os.name != "nt":
        return

    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:
        pass

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass

    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def app_root():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def clean_output(text):
    return ANSI_RE.sub("", text or "").replace("\r\n", "\n").replace("\r", "\n")


def find_ncmdump(base_dir):
    candidates = [
        base_dir / "ncmdump-1.5.1-windows-amd64" / "ncmdump.exe",
        base_dir / "ncmdump.exe",
    ]

    path_candidate = shutil.which("ncmdump.exe")
    if path_candidate:
        candidates.append(Path(path_candidate))

    for candidate in candidates:
        if candidate.is_file():
            return candidate

    for candidate in base_dir.rglob("ncmdump.exe"):
        if ".git" not in candidate.parts and candidate.is_file():
            return candidate

    return None


def run_ncmdump(engine_path, args):
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    return subprocess.run(
        [str(engine_path)] + args,
        cwd=str(Path(engine_path).resolve().parent),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        errors="replace",
        creationflags=flags,
    )


def version_text(engine_path):
    if not engine_path or not Path(engine_path).is_file():
        return ""
    try:
        result = run_ncmdump(engine_path, ["-v"])
        return clean_output(result.stdout).strip()
    except Exception:
        return ""


def ncm_files_in_folder(folder, recursive):
    pattern = "**/*.ncm" if recursive else "*.ncm"
    files = [path for path in Path(folder).glob(pattern) if path.is_file()]
    return sorted(files, key=lambda item: str(item).lower())


def expected_output_paths(source_path, output_dir):
    source_path = Path(source_path)
    target_dir = Path(output_dir) if output_dir else source_path.parent
    return [target_dir / "{}{}".format(source_path.stem, extension) for extension in AUDIO_EXTENSIONS]


def existing_converted_outputs(source_path, output_dir):
    return [path for path in expected_output_paths(source_path, output_dir) if path.exists()]


def destination_for_file(file_path, folder_root, output_root, recursive):
    if not output_root:
        return None

    destination = Path(output_root)
    if folder_root and recursive:
        try:
            relative_parent = Path(file_path).parent.relative_to(folder_root)
            destination = destination / relative_parent
        except ValueError:
            pass
    return destination


def collect_conversion_jobs(items, output_root=None, recursive=True, skip_existing=True):
    output_root = Path(output_root).resolve() if output_root else None
    jobs = []
    skipped = []
    seen = set()

    def add_file(file_path, folder_root=None):
        file_path = Path(file_path).resolve()
        if not file_path.is_file() or file_path.suffix.lower() != ".ncm":
            return

        destination = destination_for_file(file_path, folder_root, output_root, recursive)
        key = (str(file_path).lower(), str(destination).lower() if destination else "")
        if key in seen:
            return
        seen.add(key)

        existing = existing_converted_outputs(file_path, destination)
        if skip_existing and existing:
            skipped.append((file_path, existing[0]))
            return
        jobs.append((file_path, destination))

    for item in items:
        kind = item["kind"]
        path = Path(item["path"]).resolve()

        if kind == "file":
            add_file(path)
        elif kind == "folder" and path.is_dir():
            for file_path in ncm_files_in_folder(path, recursive):
                add_file(file_path, folder_root=path)

    return jobs, skipped


def choose_font_family(root):
    families = set(tkfont.families(root))
    for candidate in (
        "Segoe UI Variable Text",
        "Microsoft YaHei UI",
        "Segoe UI",
        "微软雅黑",
    ):
        if candidate in families:
            return candidate
    return "TkDefaultFont"


class Tooltip:
    def __init__(self, widget, text, delay=450):
        self.widget = widget
        self.text = text
        self.delay = delay
        self.after_id = None
        self.window = None
        widget.bind("<Enter>", self.schedule)
        widget.bind("<Leave>", self.hide)
        widget.bind("<ButtonPress>", self.hide)

    def schedule(self, _event=None):
        self.hide()
        self.after_id = self.widget.after(self.delay, self.show)

    def show(self):
        if self.window or not self.text:
            return
        x = self.widget.winfo_rootx() + 18
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
        self.window = Toplevel(self.widget)
        self.window.wm_overrideredirect(True)
        self.window.wm_geometry("+{}+{}".format(x, y))
        label = Label(
            self.window,
            text=self.text,
            justify=LEFT,
            bg="#18211e",
            fg="#f7f3ea",
            bd=0,
            padx=10,
            pady=8,
            wraplength=280,
            font=("Microsoft YaHei UI", 9),
        )
        label.pack()

    def hide(self, _event=None):
        if self.after_id:
            self.widget.after_cancel(self.after_id)
            self.after_id = None
        if self.window:
            self.window.destroy()
            self.window = None


class ScrollableFrame(ttk.Frame):
    def __init__(self, parent, bg):
        super().__init__(parent, style="Card.TFrame")
        self.canvas = Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.content = ttk.Frame(self.canvas, style="Card.TFrame")
        self.window_id = self.canvas.create_window((0, 0), window=self.content, anchor="nw")

        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side=LEFT, fill=BOTH, expand=True)
        self.scrollbar.pack(side=RIGHT, fill="y")

        self.content.bind("<Configure>", self._on_content_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind("<Enter>", self._bind_mousewheel)
        self.canvas.bind("<Leave>", self._unbind_mousewheel)

    def _on_content_configure(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self.canvas.itemconfigure(self.window_id, width=event.width)

    def _bind_mousewheel(self, _event=None):
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

    def _unbind_mousewheel(self, _event=None):
        self.canvas.unbind_all("<MouseWheel>")

    def _on_mousewheel(self, event):
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")


class NcmdumpFrontend:
    def __init__(self):
        enable_high_dpi()
        self.root_dir = app_root()
        self.root = Tk()
        self.root.title("NCM Studio")
        self.root.geometry("1080x760")
        self.root.minsize(980, 680)

        try:
            scaling = max(1.0, self.root.winfo_fpixels("1i") / 72.0)
            self.root.tk.call("tk", "scaling", scaling)
        except Exception:
            pass

        self.engine_var = StringVar()
        self.output_var = StringVar()
        self.recursive_var = BooleanVar(value=True)
        self.skip_existing_var = BooleanVar(value=True)
        self.remove_var = BooleanVar(value=False)
        self.status_var = StringVar(value="就绪")
        self.progress_var = StringVar(value="0 / 0")
        self.item_count_var = StringVar(value="0 个入口")

        self.items = []
        self.worker = None
        self.stop_event = threading.Event()
        self.message_queue = queue.Queue()
        self.current_process = None
        self.last_input_dir = str(self.root_dir)
        self.config_path = self.root_dir / CONFIG_FILENAME

        detected = find_ncmdump(self.root_dir)
        if detected:
            self.engine_var.set(str(detected))

        self.configure_style()
        self.build_ui()
        self.load_user_config()
        self.update_version_label()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(100, self.drain_messages)

    def configure_style(self):
        self.font_family = choose_font_family(self.root)
        tkfont.nametofont("TkDefaultFont").configure(family=self.font_family, size=10)
        tkfont.nametofont("TkTextFont").configure(family=self.font_family, size=10)
        tkfont.nametofont("TkMenuFont").configure(family=self.font_family, size=10)

        style = ttk.Style(self.root)
        for theme in ("vista", "xpnative", "clam"):
            if theme in style.theme_names():
                try:
                    style.theme_use(theme)
                    break
                except Exception:
                    pass

        self.colors = {
            "bg": "#f3f0e8",
            "panel": "#fffdf7",
            "panel_alt": "#f8f5ee",
            "ink": "#18211e",
            "muted": "#6e746e",
            "line": "#d8d2c5",
            "accent": "#006f63",
            "accent_dark": "#00564d",
            "gold": "#9f7a2e",
            "warn": "#9a3412",
        }

        self.root.configure(bg=self.colors["bg"])
        style.configure(".", font=(self.font_family, 10), background=self.colors["bg"], foreground=self.colors["ink"])
        style.configure("TFrame", background=self.colors["bg"])
        style.configure("Card.TFrame", background=self.colors["panel"], relief="solid", borderwidth=1)
        style.configure("Soft.TFrame", background=self.colors["panel_alt"])
        style.configure("TLabel", background=self.colors["bg"], foreground=self.colors["ink"])
        style.configure("Card.TLabel", background=self.colors["panel"], foreground=self.colors["ink"])
        style.configure("Soft.TLabel", background=self.colors["panel_alt"], foreground=self.colors["ink"])
        style.configure("Hero.TLabel", font=(self.font_family, 22, "bold"), background=self.colors["bg"], foreground=self.colors["ink"])
        style.configure("Eyebrow.TLabel", font=(self.font_family, 9, "bold"), background=self.colors["bg"], foreground=self.colors["gold"])
        style.configure("Sub.TLabel", font=(self.font_family, 9), background=self.colors["bg"], foreground=self.colors["muted"])
        style.configure("Section.TLabel", font=(self.font_family, 10, "bold"), background=self.colors["panel"], foreground=self.colors["ink"])
        style.configure("Hint.TLabel", font=(self.font_family, 9), background=self.colors["panel"], foreground=self.colors["muted"])
        style.configure("Tiny.TLabel", font=(self.font_family, 8), background=self.colors["panel"], foreground=self.colors["muted"])
        style.configure("Badge.TLabel", font=(self.font_family, 9, "bold"), background="#e8efe9", foreground=self.colors["accent"])
        style.configure("TButton", padding=(12, 7), font=(self.font_family, 10))
        style.configure("Accent.TButton", background=self.colors["accent"], foreground="#ffffff", padding=(16, 10), font=(self.font_family, 10, "bold"))
        style.map("Accent.TButton", background=[("active", self.colors["accent_dark"]), ("disabled", "#90aaa6")])
        style.configure("Ghost.TButton", padding=(10, 6))
        style.configure("Card.TCheckbutton", background=self.colors["panel"], foreground=self.colors["ink"], font=(self.font_family, 10))
        style.configure("Danger.TCheckbutton", background=self.colors["panel"], foreground=self.colors["warn"], font=(self.font_family, 10))
        style.configure("Horizontal.TProgressbar", troughcolor="#e2ddd3", background=self.colors["accent"])

    def build_ui(self):
        outer = ttk.Frame(self.root, padding=22)
        outer.pack(fill=BOTH, expand=True)

        header = ttk.Frame(outer)
        header.pack(fill=X, pady=(0, 16))
        title_block = ttk.Frame(header)
        title_block.pack(side=LEFT, fill=X, expand=True)
        ttk.Label(title_block, text="LOCAL AUDIO UTILITY", style="Eyebrow.TLabel").pack(anchor=W)
        ttk.Label(title_block, text="NCM Studio", style="Hero.TLabel").pack(anchor=W)
        ttk.Label(title_block, text="把网易云缓存转换成可播放的 MP3 / FLAC", style="Sub.TLabel").pack(anchor=W, pady=(2, 0))
        ttk.Label(header, textvariable=self.status_var, style="Badge.TLabel", padding=(12, 7)).pack(side=RIGHT, anchor="ne")

        engine_panel = ttk.Frame(outer, style="Card.TFrame", padding=14)
        engine_panel.pack(fill=X, pady=(0, 14))
        engine_top = ttk.Frame(engine_panel, style="Card.TFrame")
        engine_top.pack(fill=X)
        ttk.Label(engine_top, text="转换核心", style="Section.TLabel").pack(side=LEFT)
        self.version_label = ttk.Label(engine_top, text="", style="Tiny.TLabel")
        self.version_label.pack(side=RIGHT)
        engine_row = ttk.Frame(engine_panel, style="Card.TFrame")
        engine_row.pack(fill=X, pady=(10, 0))
        self.engine_entry = ttk.Entry(engine_row, textvariable=self.engine_var)
        self.engine_entry.pack(side=LEFT, fill=X, expand=True, ipady=3)
        ttk.Button(engine_row, text="选择", command=self.choose_engine, style="Ghost.TButton").pack(side=LEFT, padx=(8, 0))
        ttk.Button(engine_row, text="自动查找", command=self.auto_find_engine, style="Ghost.TButton").pack(side=LEFT, padx=(8, 0))

        action_panel = ttk.Frame(outer, style="Card.TFrame", padding=14)
        action_panel.pack(fill=X, pady=(0, 14))
        action_panel.columnconfigure(0, weight=0)
        action_panel.columnconfigure(1, weight=0)
        action_panel.columnconfigure(2, weight=0)
        action_panel.columnconfigure(3, weight=0)
        action_panel.columnconfigure(4, weight=1)

        self.start_button = Button(
            action_panel,
            text="开始转换",
            command=self.start_conversion,
            bg="#004f46",
            fg="#ffffff",
            activebackground="#003a34",
            activeforeground="#ffffff",
            disabledforeground="#eef6f3",
            relief="flat",
            bd=0,
            cursor="hand2",
            font=(self.font_family, 11, "bold"),
            padx=24,
            pady=10,
        )
        self.start_button.grid(row=0, column=0, sticky="w")
        self.stop_button = ttk.Button(action_panel, text="停止", command=self.stop_conversion, state="disabled", style="Ghost.TButton", width=10)
        self.stop_button.grid(row=0, column=1, padx=(10, 0), sticky="w")
        ttk.Button(action_panel, text="扫描", command=self.dedupe_now, style="Ghost.TButton", width=12).grid(row=0, column=2, padx=(10, 0), sticky="w")
        ttk.Button(action_panel, text="打开输出目录", command=self.open_output_folder, style="Ghost.TButton", width=14).grid(row=0, column=3, padx=(10, 18), sticky="w")
        status_frame = ttk.Frame(action_panel, style="Soft.TFrame", padding=(12, 8))
        status_frame.grid(row=0, column=4, sticky="ew")
        ttk.Label(status_frame, textvariable=self.progress_var, style="Soft.TLabel").pack(anchor=W)
        self.progress = ttk.Progressbar(status_frame, mode="determinate")
        self.progress.pack(fill=X, pady=(6, 0))

        body = ttk.Frame(outer)
        body.pack(fill=BOTH, expand=True)

        left_panel = ttk.Frame(body, style="Card.TFrame", padding=14)
        left_panel.pack(side=LEFT, fill=BOTH, expand=True, padx=(0, 14))
        right_panel = ttk.Frame(body, style="Card.TFrame", padding=0)
        right_panel.pack(side=RIGHT, fill=BOTH)
        right_panel.pack_propagate(False)
        right_panel.configure(width=330)
        right_scroll = ScrollableFrame(right_panel, self.colors["panel"])
        right_scroll.pack(fill=BOTH, expand=True, padx=14, pady=14)
        right_panel = right_scroll.content

        list_header = ttk.Frame(left_panel, style="Card.TFrame")
        list_header.pack(fill=X)
        ttk.Label(list_header, text="待转换队列", style="Section.TLabel").pack(side=LEFT)
        ttk.Label(list_header, textvariable=self.item_count_var, style="Tiny.TLabel").pack(side=LEFT, padx=(10, 0), pady=(2, 0))
        ttk.Button(list_header, text="添加文件", command=self.add_files, style="Ghost.TButton").pack(side=RIGHT, padx=(8, 0))
        ttk.Button(list_header, text="添加文件夹", command=self.add_folder, style="Ghost.TButton").pack(side=RIGHT)

        self.listbox = self.create_listbox(left_panel)
        self.listbox.pack(fill=BOTH, expand=True, pady=12)

        list_actions = ttk.Frame(left_panel, style="Card.TFrame")
        list_actions.pack(fill=X)
        ttk.Button(list_actions, text="移除选中", command=self.remove_selected, style="Ghost.TButton").pack(side=LEFT)
        ttk.Button(list_actions, text="清空", command=self.clear_items, style="Ghost.TButton").pack(side=LEFT, padx=(8, 0))

        ttk.Label(right_panel, text="输出", style="Section.TLabel").pack(anchor=W)
        output_row = ttk.Frame(right_panel, style="Card.TFrame")
        output_row.pack(fill=X, pady=(10, 6))
        output_row.columnconfigure(0, weight=1)
        ttk.Entry(output_row, textvariable=self.output_var).grid(row=0, column=0, sticky="ew", ipady=3)
        ttk.Button(output_row, text="选择", command=self.choose_output, style="Ghost.TButton", width=6).grid(row=0, column=1, padx=(8, 0), sticky="e")
        ttk.Label(right_panel, text="留空时输出到源文件旁边。", style="Hint.TLabel", wraplength=260).pack(anchor=W, pady=(0, 16))

        ttk.Label(right_panel, text="转换策略", style="Section.TLabel").pack(anchor=W)
        recursive_check = ttk.Checkbutton(right_panel, text="递归文件夹", variable=self.recursive_var, style="Card.TCheckbutton")
        recursive_check.pack(anchor=W, pady=(10, 3))
        recursive_hint = ttk.Label(
            right_panel,
            text="开启后会扫描子文件夹；指定输出目录时会保留原目录结构。",
            style="Hint.TLabel",
            wraplength=260,
        )
        recursive_hint.pack(anchor=W, pady=(0, 10))
        Tooltip(recursive_check, "例如选择 D:\\Music，开启后会处理 D:\\Music\\子目录 里的 .ncm。指定输出目录时，子目录层级会一起保留。")

        ttk.Checkbutton(right_panel, text="跳过已转换", variable=self.skip_existing_var, style="Card.TCheckbutton").pack(anchor=W, pady=(0, 3))
        ttk.Label(
            right_panel,
            text="输出目录里已有同名 .mp3 或 .flac 时自动跳过。",
            style="Hint.TLabel",
            wraplength=260,
        ).pack(anchor=W, pady=(0, 10))
        ttk.Checkbutton(right_panel, text="成功后删除源文件", variable=self.remove_var, style="Danger.TCheckbutton").pack(anchor=W, pady=(0, 10))
        ttk.Label(
            right_panel,
            text="主操作按钮固定在上方操作带，不会因为窗口高度变化被侧栏裁掉。",
            style="Hint.TLabel",
            wraplength=260,
        ).pack(anchor=W)

        log_panel = ttk.Frame(outer, style="Card.TFrame", padding=14)
        log_panel.pack(fill=BOTH, expand=False, pady=(14, 0))
        ttk.Label(log_panel, text="日志", style="Section.TLabel").pack(anchor=W)
        self.log_text = scrolledtext.ScrolledText(
            log_panel,
            height=8,
            bg="#151a17",
            fg="#f0ece1",
            insertbackground="#f0ece1",
            selectbackground="#2d6159",
            relief="flat",
            font=("Cascadia Mono", 10),
        )
        self.log_text.pack(fill=BOTH, expand=True, pady=(10, 0))
        self.log("已就绪。")

    def create_listbox(self, parent):
        listbox = Listbox(
            parent,
            activestyle="none",
            bg="#fffefa",
            fg=self.colors["ink"],
            selectbackground=self.colors["accent"],
            selectforeground="#ffffff",
            highlightthickness=1,
            highlightbackground=self.colors["line"],
            relief="flat",
            font=(self.font_family, 10),
            selectmode="extended",
        )
        return listbox

    def choose_engine(self):
        path = filedialog.askopenfilename(
            title="选择 ncmdump.exe",
            filetypes=[("ncmdump.exe", "ncmdump.exe"), ("EXE", "*.exe"), ("全部文件", "*.*")],
            initialdir=str(self.root_dir),
        )
        if path:
            self.engine_var.set(path)
            self.update_version_label()
            self.save_user_config()

    def auto_find_engine(self):
        detected = find_ncmdump(self.root_dir)
        if detected:
            self.engine_var.set(str(detected))
            self.update_version_label()
            self.log("已找到转换核心: {}".format(detected))
            self.save_user_config()
        else:
            messagebox.showwarning("未找到", "没有找到 ncmdump.exe。")

    def update_version_label(self):
        text = version_text(self.engine_var.get())
        self.version_label.configure(text=text or "未验证")

    def load_user_config(self):
        if not self.config_path.is_file():
            return

        try:
            with self.config_path.open("r", encoding="utf-8") as config_file:
                data = json.load(config_file)
        except Exception:
            return

        engine = data.get("engine_path")
        if engine and Path(engine).is_file():
            self.engine_var.set(engine)

        output_dir = data.get("output_dir")
        if output_dir:
            self.output_var.set(output_dir)

        last_input_dir = data.get("last_input_dir")
        if last_input_dir:
            self.last_input_dir = last_input_dir

        self.recursive_var.set(bool(data.get("recursive", self.recursive_var.get())))
        self.skip_existing_var.set(bool(data.get("skip_existing", self.skip_existing_var.get())))
        self.remove_var.set(bool(data.get("remove_source", self.remove_var.get())))

        for item in data.get("items", []):
            kind = item.get("kind")
            path = item.get("path")
            if kind in ("file", "folder") and path and Path(path).exists():
                self.add_item(kind, Path(path))

    def save_user_config(self):
        items = []
        for item in self.items:
            path = Path(item["path"])
            if path.exists():
                items.append({"kind": item["kind"], "path": str(path)})

        data = {
            "engine_path": self.engine_var.get().strip(),
            "output_dir": self.output_var.get().strip(),
            "last_input_dir": self.last_input_dir,
            "recursive": self.recursive_var.get(),
            "skip_existing": self.skip_existing_var.get(),
            "remove_source": self.remove_var.get(),
            "items": items,
        }

        try:
            with self.config_path.open("w", encoding="utf-8") as config_file:
                json.dump(data, config_file, ensure_ascii=False, indent=2)
        except Exception as exc:
            self.log("保存配置失败: {}".format(exc))

    def on_close(self):
        self.save_user_config()
        self.root.destroy()

    def input_initial_dir(self):
        if self.last_input_dir and Path(self.last_input_dir).exists():
            return self.last_input_dir
        for item in self.items:
            path = Path(item["path"])
            if item["kind"] == "folder" and path.exists():
                return str(path)
            if item["kind"] == "file" and path.parent.exists():
                return str(path.parent)
        return str(self.root_dir)

    def output_initial_dir(self):
        output_dir = self.output_var.get().strip()
        if output_dir and Path(output_dir).exists():
            return output_dir
        return self.input_initial_dir()

    def choose_output(self):
        path = filedialog.askdirectory(title="选择输出目录", initialdir=self.output_initial_dir())
        if path:
            self.output_var.set(path)
            self.save_user_config()

    def add_files(self):
        paths = filedialog.askopenfilenames(
            title="选择 NCM 文件",
            filetypes=[("NCM 文件", "*.ncm"), ("全部文件", "*.*")],
            initialdir=self.input_initial_dir(),
        )
        for path in paths:
            self.add_item("file", Path(path))
        if paths:
            self.last_input_dir = str(Path(paths[0]).parent)
            self.save_user_config()

    def add_folder(self):
        path = filedialog.askdirectory(title="选择文件夹", initialdir=self.input_initial_dir())
        if path:
            self.last_input_dir = path
            self.add_item("folder", Path(path))
            self.save_user_config()

    def add_item(self, kind, path):
        path = Path(path).resolve()
        key = (kind, str(path).lower())
        existing = {(item["kind"], str(item["path"]).lower()) for item in self.items}
        if key in existing:
            return

        self.items.append({"kind": kind, "path": path})
        prefix = "文件" if kind == "file" else "文件夹"
        self.listbox.insert(END, "[{}] {}".format(prefix, path))
        self.refresh_item_count()

    def refresh_item_count(self):
        self.item_count_var.set("{} 个入口".format(len(self.items)))

    def remove_selected(self):
        selected = list(self.listbox.curselection())
        selected.reverse()
        for index in selected:
            self.listbox.delete(index)
            del self.items[index]
        self.refresh_item_count()
        self.save_user_config()

    def clear_items(self):
        self.items = []
        self.listbox.delete(0, END)
        self.refresh_item_count()
        self.save_user_config()

    def build_jobs(self, skip_existing=None):
        if skip_existing is None:
            skip_existing = self.skip_existing_var.get()
        output_root = self.output_var.get().strip() or None
        return collect_conversion_jobs(
            self.items,
            output_root=output_root,
            recursive=self.recursive_var.get(),
            skip_existing=skip_existing,
        )

    def job_output_hint(self, source, output_dir):
        target_dir = Path(output_dir) if output_dir else Path(source).parent
        return "{}\\{}.(mp3/flac)".format(target_dir, Path(source).stem)

    def log_pending_jobs(self, jobs, limit=60):
        if not jobs:
            self.log("待转换：0 个。")
            return

        self.log("待转换清单：{} 个。".format(len(jobs)))
        for source, output_dir in jobs[:limit]:
            self.log("[待转换] {} -> {}".format(Path(source).name, self.job_output_hint(source, output_dir)))
        if len(jobs) > limit:
            self.log("还有 {} 个待转换文件未展开显示。".format(len(jobs) - limit))

    def log_skipped_jobs(self, skipped, limit=20):
        if not skipped:
            self.log("已转换跳过：0 个。")
            return

        self.log("已转换跳过：{} 个。".format(len(skipped)))
        for source, existing in skipped[:limit]:
            self.log("[跳过] {} -> 已存在 {}".format(source.name, existing))
        if len(skipped) > limit:
            self.log("还有 {} 个已转换文件未展开显示。".format(len(skipped) - limit))

    def dedupe_now(self):
        if not self.items:
            messagebox.showwarning("没有文件", "请先添加 NCM 文件或文件夹。")
            return

        self.skip_existing_var.set(True)
        jobs, skipped = self.build_jobs(skip_existing=True)
        total = len(jobs) + len(skipped)
        if total == 0:
            messagebox.showwarning("没有 NCM 文件", "没有找到可转换的 .ncm 文件。")
            return

        self.log("\n扫描结果：共发现 {} 个 NCM，已转换 {} 个，待转换 {} 个。".format(total, len(skipped), len(jobs)))
        self.log_pending_jobs(jobs)
        self.log_skipped_jobs(skipped)

        self.status_var.set("去重完成")
        self.progress.configure(maximum=max(total, 1), value=len(skipped))
        self.progress_var.set("跳过 {} / 待转换 {}".format(len(skipped), len(jobs)))

    def start_conversion(self):
        engine = Path(self.engine_var.get())
        if not engine.is_file():
            messagebox.showerror("缺少转换核心", "请选择有效的 ncmdump.exe。")
            return
        if not self.items:
            messagebox.showwarning("没有文件", "请先添加 NCM 文件或文件夹。")
            return
        if self.remove_var.get():
            ok = messagebox.askyesno("确认删除源文件", "转换成功后会删除源 .ncm 文件，确定继续吗？")
            if not ok:
                return

        jobs, skipped = self.build_jobs()
        if jobs:
            self.log("\n即将转换：{} 个文件。".format(len(jobs)))
            self.log_pending_jobs(jobs)

        if skipped:
            self.log("\n已启用去重。")
            self.log_skipped_jobs(skipped, limit=12)

        if not jobs:
            if skipped:
                self.status_var.set("全部已存在")
                self.progress.configure(maximum=max(len(skipped), 1), value=len(skipped))
                self.progress_var.set("跳过 {} / 待转换 0".format(len(skipped)))
                messagebox.showinfo("无需转换", "输出目录里已经存在对应的 MP3/FLAC 文件。")
            else:
                messagebox.showwarning("没有 NCM 文件", "没有找到可转换的 .ncm 文件。")
            return

        self.stop_event.clear()
        self.progress.configure(maximum=len(jobs), value=0)
        self.progress_var.set("0 / {}".format(len(jobs)))
        self.status_var.set("转换中")
        self.set_running(True)
        self.log("\n开始转换，共 {} 个文件，跳过 {} 个。".format(len(jobs), len(skipped)))

        self.worker = threading.Thread(
            target=self.run_jobs,
            args=(engine, jobs, self.remove_var.get(), len(skipped)),
            daemon=True,
        )
        self.worker.start()

    def run_jobs(self, engine, jobs, remove_source, skipped_count):
        ok_count = 0
        fail_count = 0
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

        for index, (source, output_dir) in enumerate(jobs, start=1):
            if self.stop_event.is_set():
                break

            args = [str(engine), str(source)]
            if output_dir:
                output_dir.mkdir(parents=True, exist_ok=True)
                args.extend(["-o", str(output_dir)])
            if remove_source:
                args.append("-m")

            self.message_queue.put(("status", "转换中: {}".format(source.name), index - 1, len(jobs)))
            self.message_queue.put(("log", "\n> {}".format(" ".join('"{}"'.format(arg) if " " in arg else arg for arg in args))))

            try:
                self.current_process = subprocess.Popen(
                    args,
                    cwd=str(engine.parent),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=flags,
                )
                stdout, _ = self.current_process.communicate()
                output = clean_output(stdout)
                if output.strip():
                    self.message_queue.put(("log", output.strip()))
                if self.current_process.returncode == 0:
                    ok_count += 1
                else:
                    fail_count += 1
                    self.message_queue.put(("log", "[失败] {}，退出码 {}".format(source, self.current_process.returncode)))
            except Exception as exc:
                fail_count += 1
                self.message_queue.put(("log", "[异常] {}: {}".format(source, exc)))
            finally:
                self.current_process = None
                self.message_queue.put(("progress", index, len(jobs)))

        if self.stop_event.is_set():
            self.message_queue.put(("done", "已停止。成功 {} 个，失败 {} 个，跳过 {} 个。".format(ok_count, fail_count, skipped_count)))
        else:
            self.message_queue.put(("done", "完成。成功 {} 个，失败 {} 个，跳过 {} 个。".format(ok_count, fail_count, skipped_count)))

    def stop_conversion(self):
        self.stop_event.set()
        if self.current_process and self.current_process.poll() is None:
            try:
                self.current_process.terminate()
            except Exception:
                pass
        self.status_var.set("正在停止")

    def set_running(self, running):
        if running:
            self.start_button.configure(state="disabled", bg="#8aa29d", cursor="arrow")
        else:
            self.start_button.configure(state="normal", bg="#004f46", cursor="hand2")
        self.stop_button.configure(state="normal" if running else "disabled")

    def drain_messages(self):
        try:
            while True:
                message = self.message_queue.get_nowait()
                kind = message[0]
                if kind == "log":
                    self.log(message[1])
                elif kind == "status":
                    _, text, done, total = message
                    self.status_var.set(text)
                    self.progress_var.set("{} / {}".format(done, total))
                elif kind == "progress":
                    _, done, total = message
                    self.progress.configure(value=done)
                    self.progress_var.set("{} / {}".format(done, total))
                elif kind == "done":
                    self.status_var.set(message[1])
                    self.log(message[1])
                    self.set_running(False)
        except queue.Empty:
            pass
        self.root.after(100, self.drain_messages)

    def log(self, text):
        self.log_text.insert(END, text + "\n")
        self.log_text.see(END)

    def open_output_folder(self):
        target = self.output_var.get().strip()
        if not target:
            if self.items:
                first = self.items[0]["path"]
                target = str(first.parent if self.items[0]["kind"] == "file" else first)
            else:
                target = str(self.root_dir)
        path = Path(target)
        if path.exists():
            os.startfile(str(path))
        else:
            messagebox.showwarning("目录不存在", "输出目录不存在。")

    def run(self):
        self.root.mainloop()


def self_test():
    base = app_root()
    engine = find_ncmdump(base)
    sample = base / "ncmdump" / "test" / "test.ncm"
    output_dir = base / "gui-smoke-output"
    jobs, skipped = collect_conversion_jobs(
        [{"kind": "file", "path": sample}],
        output_root=output_dir,
        recursive=True,
        skip_existing=True,
    )
    print("root={}".format(base))
    print("engine={}".format(engine or ""))
    print("version={}".format(version_text(engine) if engine else ""))
    print("dedupe_jobs={}".format(len(jobs)))
    print("dedupe_skipped={}".format(len(skipped)))
    return 0 if engine else 1


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        sys.exit(self_test())
    NcmdumpFrontend().run()
