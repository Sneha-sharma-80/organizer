#!/usr/bin/env python3
"""
Advanced File Organizer - Colorful Tkinter GUI Version
With Email Notification on completion
"""

import json, logging, shutil, sys, threading, time, hashlib, os
import sched
from tkinter import simpledialog
from datetime import datetime
from pathlib import Path
from queue import Queue, Empty
from typing import Dict, List, Optional
import smtplib
from email.message import EmailMessage  # <-- Email module

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    WATCHDOG_AVAILABLE = True
except Exception:
    WATCHDOG_AVAILABLE = False

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

try:
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    MATPLOTLIB_AVAILABLE = True
except Exception:
    MATPLOTLIB_AVAILABLE = False

# ---------- Defaults ----------
DEFAULT_EXT_MAP = {
    "Images": [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".webp", ".heic"],
    "Videos": [".mp4", ".mkv", ".mov", ".avi", ".wmv", ".flv", ".webm"],
    "Documents": [".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".odt", ".txt", ".md"],
    "Archives": [".zip", ".rar", ".7z", ".tar", ".gz", ".bz2"],
    "Code": [".py", ".js", ".ts", ".java", ".c", ".cpp", ".cs", ".html", ".css", ".go", ".rs"],
    "Music": [".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg"],
}
DEFAULT_OTHER = "Others"
CONFIG_FILENAME = "config.json"
LOG_FILENAME = "organizer.log"
MOVE_HISTORY = "move_history.json"

# ---------- Logging ----------
logger = logging.getLogger("advanced_organizer")
logger.setLevel(logging.DEBUG)
fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
ch = logging.StreamHandler()
ch.setLevel(logging.INFO)
ch.setFormatter(fmt)
logger.addHandler(ch)
fh = logging.FileHandler(LOG_FILENAME, encoding="utf-8")
fh.setLevel(logging.DEBUG)
fh.setFormatter(fmt)
logger.addHandler(fh)

# ---------- Helper Functions ----------
def load_config(config_path: Optional[Path] = None) -> Dict[str, List[str]]:
    if config_path is None:
        config_path = Path(CONFIG_FILENAME)
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            norm = {}
            for k, v in data.items():
                norm[k] = [ext.lower() if ext.startswith(".") else f".{ext.lower()}" for ext in v]
            logger.info(f"Loaded config from {config_path}")
            return norm
        except Exception as e:
            logger.error(f"Failed to load config.json: {e}")
            messagebox.showwarning("Config load error", f"Failed to load {config_path}: {e}")
            return DEFAULT_EXT_MAP.copy()
    else:
        return DEFAULT_EXT_MAP.copy()

def find_category_by_ext(ext_map: Dict[str, List[str]], ext: str) -> str:
    ext = ext.lower()
    for cat, exts in ext_map.items():
        if ext in exts:
            return cat
    return DEFAULT_OTHER

def make_unique_path(dest: Path) -> Path:
    if not dest.exists():
        return dest
    parent = dest.parent
    stem = dest.stem
    suffix = dest.suffix
    counter = 1
    while True:
        new_name = f"{stem}_{counter}{suffix}"
        candidate = parent / new_name
        if not candidate.exists():
            return candidate
        counter += 1

def save_move_history(history: List[Dict]):
    try:
        with open(MOVE_HISTORY, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, default=str)
        logger.debug(f"Saved move history ({len(history)} records) to {MOVE_HISTORY}")
    except Exception as e:
        logger.error(f"Failed to save move history: {e}")

def load_move_history() -> List[Dict]:
    if not Path(MOVE_HISTORY).exists():
        return []
    try:
        with open(MOVE_HISTORY, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load move history: {e}")
        return []

def list_files(target: Path, recursive: bool) -> List[Path]:
    if recursive:
        return [p for p in target.rglob("*") if p.is_file()]
    else:
        return [p for p in target.iterdir() if p.is_file()]

def organize_by_type(target: Path, ext_map: Dict[str, List[str]], dry_run: bool, recursive: bool) -> List[Dict]:
    files = list_files(target, recursive)
    moves = []
    for f in files:
        ext = f.suffix.lower()
        cat = find_category_by_ext(ext_map, ext)
        dest_dir = target / cat
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = make_unique_path(dest_dir / f.name)
        move_record = {"src": str(f), "dst": str(dest), "time": datetime.utcnow().isoformat(), "dry": dry_run}
        if not dry_run:
            shutil.move(str(f), str(dest))
        moves.append(move_record)
    return moves

def organize_by_date(target: Path, dry_run: bool, recursive: bool) -> List[Dict]:
    files = list_files(target, recursive)
    moves = []
    for f in files:
        mtime = datetime.fromtimestamp(f.stat().st_mtime)
        folder_name = f"{mtime.year}-{mtime.month:02d}"
        dest_dir = target / folder_name
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = make_unique_path(dest_dir / f.name)
        move_record = {"src": str(f), "dst": str(dest), "time": datetime.utcnow().isoformat(), "dry": dry_run}
        if not dry_run:
            shutil.move(str(f), str(dest))
        moves.append(move_record)
    return moves

def get_file_hash(path: Path, block_size: int = 65536) -> Optional[str]:
    try:
        md5 = hashlib.md5()
        with open(path, "rb") as f:
            while chunk := f.read(block_size):
                md5.update(chunk)
        return md5.hexdigest()
    except Exception:
        return None

def find_duplicates(target: Path, recursive: bool) -> Dict[str, List[str]]:
    files = list_files(target, recursive)
    hash_map: Dict[str, List[str]] = {}
    for f in files:
        h = get_file_hash(f)
        if not h:
            continue
        hash_map.setdefault(h, []).append(str(f))
    return {h: paths for h, paths in hash_map.items() if len(paths) > 1}

def gather_stats_for_dashboard(target: Path, ext_map: Dict[str, List[str]], recursive: bool) -> Dict:
    counts = {cat: 0 for cat in ext_map.keys()}
    counts[DEFAULT_OTHER] = 0
    total_files = 0
    total_size = 0
    files = list_files(target, recursive)
    for f in files:
        cat = find_category_by_ext(ext_map, f.suffix)
        counts[cat] += 1
        total_files += 1
        try:
            total_size += f.stat().st_size
        except Exception:
            pass
    return {"total_files": total_files, "total_size_mb": round(total_size/(1024*1024),2), "counts": counts}

# ---------- Email Notification Function ----------
def send_email_notification(subject: str, body: str, to_email: str, from_email: str, password: str, smtp_server="smtp.gmail.com", smtp_port=587):
    try:
        msg = EmailMessage()
        msg.set_content(body)
        msg["Subject"] = subject
        msg["From"] = from_email
        msg["To"] = to_email
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(from_email, password)
            server.send_message(msg)
        logger.info(f"Email sent to {to_email}")
    except Exception as e:
        logger.error(f"Failed to send email: {e}")

# ---------- GUI ----------
class OrganizerGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("🎨 Advanced File Organizer")
        self.root.geometry("960x680")
        self.root.configure(bg="#f0f8ff")
        self.ext_map = load_config()
        self.task_queue = Queue()
        self.observer = None
        self.current_target = None
        self.scheduler = sched.scheduler(time.time, time.sleep)
        self.scheduled_task = None

        # ttk Style
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TLabel", background="#f0f8ff", font=("Segoe UI", 11))
        style.configure("TButton", font=("Segoe UI", 10, "bold"), foreground="white", background="#1e90ff")
        style.map("TButton", background=[("active", "#63b8ff")])
        style.configure("TRadiobutton", background="#f0f8ff")
        style.configure("TCheckbutton", background="#f0f8ff")
        style.configure("Treeview", background="white", foreground="black", rowheight=24, fieldbackground="white")
        style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"))

        self._build_ui()
        self._attach_periodic_check()

    # ---------- small helper to brighten hex color for hover ----------
    def _brighten(self, hex_color: str, amount: float = 0.12) -> str:
        # Accepts "#rrggbb"
        try:
            hex_color = hex_color.lstrip("#")
            r = int(hex_color[0:2], 16)
            g = int(hex_color[2:4], 16)
            b = int(hex_color[4:6], 16)
            r = min(255, int(r * (1 + amount)))
            g = min(255, int(g * (1 + amount)))
            b = min(255, int(b * (1 + amount)))
            return f"#{r:02x}{g:02x}{b:02x}"
        except Exception:
            return hex_color

    # ---------- Shadow + Hover Button Helper ----------
    def create_shadow_button(self, parent, text, command, width: Optional[int]=None, bg="#1e90ff", fg="white"):
        """
        Returns a Frame widget containing a button with simple shadow + hover effect.
        Use .pack() / .grid() on the returned frame.
        """
        # wrapper for shadow (darker border to simulate shadow)
        wrapper = tk.Frame(parent, bg="#2b2b2b", bd=0)
        btn = tk.Button(wrapper, text=text, command=command, bg=bg, fg=fg,
                        activebackground=self._brighten(bg, 0.08), relief=tk.FLAT,
                        font=("Segoe UI", 10, "bold"), padx=10, pady=6)
        if width:
            btn.config(width=width)
        btn.pack(padx=3, pady=3)
        # hover effect
        def on_enter(e):
            try:
                btn['bg'] = self._brighten(bg, 0.12)
            except: pass
        def on_leave(e):
            try:
                btn['bg'] = bg
            except: pass
        btn.bind("<Enter>", on_enter)
        btn.bind("<Leave>", on_leave)
        return wrapper

    def _build_ui(self):
        pad = 8
        frame = ttk.Frame(self.root, padding=pad)
        frame.pack(fill=tk.BOTH, expand=True)
        top = ttk.Frame(frame)
        top.pack(fill=tk.X, pady=(0,6))
        ttk.Label(top, text="Target folder:").grid(row=0, column=0, sticky=tk.W)
        self.target_var = tk.StringVar()
        self.target_entry = ttk.Entry(top, textvariable=self.target_var, width=70)
        self.target_entry.grid(row=0, column=1, padx=(6,6), sticky=tk.W)
        # use shadow button
        self.create_shadow_button(top, "Browse", self.browse_folder, bg="#FF7F50").grid(row=0, column=2, padx=(6,0))

        # Options
        opt_frame = ttk.LabelFrame(frame, text="Options", padding=pad)
        opt_frame.pack(fill=tk.X, pady=(0,6))
        self.mode_var = tk.StringVar(value="type")
        ttk.Radiobutton(opt_frame, text="By Type", variable=self.mode_var, value="type").grid(row=0,column=0,padx=6)
        ttk.Radiobutton(opt_frame, text="By Date", variable=self.mode_var, value="date").grid(row=0,column=1,padx=6)
        self.dry_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opt_frame, text="Dry-run", variable=self.dry_var).grid(row=0,column=2,padx=6)
        self.recursive_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opt_frame, text="Recursive", variable=self.recursive_var).grid(row=0,column=3,padx=6)

        # Buttons
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=tk.X, pady=(0,6))
        self.create_shadow_button(btn_frame, "Run Organizer", self.run_organize, bg="#1e90ff").pack(side=tk.LEFT,padx=6)
        self.create_shadow_button(btn_frame, "Undo Last", self.undo_last, bg="#6C5CE7").pack(side=tk.LEFT,padx=6)
        self.create_shadow_button(btn_frame, "Find Duplicates", self.find_duplicates_gui, bg="#00b894").pack(side=tk.LEFT,padx=6)
        self.create_shadow_button(btn_frame, "View Dashboard", self.view_dashboard_gui, bg="#fdcb6e").pack(side=tk.LEFT,padx=6)
        self.create_shadow_button(btn_frame, "Open Log File", self.open_log_file, bg="#e17055").pack(side=tk.LEFT,padx=6)
        self.create_shadow_button(btn_frame, "Start Watchdog", self.start_watchdog, bg="#00cec9").pack(side=tk.LEFT,padx=6)
        # replaced default ttk schedule button with shadow button
        self.create_shadow_button(btn_frame, "Schedule Organizer", self.schedule_organizer_gui, bg="#0984e3").pack(side=tk.LEFT, padx=6)
        self.create_shadow_button(btn_frame, "Verify File Integrity", self.verify_file_integrity, bg="#d63031").pack(side="left", padx=6)

        # Console
        bottom = ttk.LabelFrame(frame, text="Console / Activity", padding=pad)
        bottom.pack(fill=tk.BOTH, expand=True, pady=(0,6))
        self.console = scrolledtext.ScrolledText(bottom, height=12, state=tk.DISABLED, bg="#e6f2ff")
        self.console.pack(fill=tk.BOTH, expand=True)

    def browse_folder(self):
        path = filedialog.askdirectory()
        if path:
            self.target_var.set(path)
            self.current_target = Path(path)

    def log(self,msg:str):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"{ts} - {msg}\n"
        self.console.configure(state=tk.NORMAL)
        self.console.insert(tk.END,line)
        self.console.see(tk.END)
        self.console.configure(state=tk.DISABLED)
        logger.info(msg)

    # ---------- Organize ----------
    def run_organize(self):
        target = self.target_var.get().strip()
        if not target: return
        target_path = Path(target).expanduser().resolve()
        self.current_target = target_path
        dry = self.dry_var.get()
        recursive = self.recursive_var.get()
        mode = self.mode_var.get()

        def worker():
            self.log(f"Starting organize ({mode}) dry={dry} recursive={recursive}")
            try:
                if mode=="type":
                    moves=organize_by_type(target_path,self.ext_map,dry,recursive)
                else:
                    moves=organize_by_date(target_path,dry,recursive)
                if moves and not dry:
                    history = load_move_history()
                    history.append({"run_time":datetime.utcnow().isoformat(),"target":str(target_path),"moves":moves})
                    save_move_history(history)
                self.log(f"Organize completed: {len(moves)} files")
                send_email_notification(
                    subject="File Organizer Completed ✅",
                    body=f"Organizer run finished.\nTarget folder: {target_path}\nFiles moved: {len(moves)}",
                    to_email="sharma807790@gmail.com",        # 👈 yahan apna email likho (jahan pe notification chahiye)
                    from_email="sharma807790@gmail.com",      # 👈 apna Gmail likho (jo sender hoga)
                    password="vjbr iakc biny bqjb"            # 👈 Gmail App Password (4 spaces wala 16 char ka code)
                )

                # ----- Email Notification fallback (kept for compatibility) -----
                try:
                    send_email_notification(
                        subject="File Organizer Completed ✅",
                        body=f"Organizer run finished.\nTarget folder: {target_path}\nFiles moved: {len(moves)}",
                        to_email="recipient@example.com",   # Replace with your email
                        from_email="your_email@gmail.com",  # Replace with your email
                        password="your_app_password"        # Gmail App Password
                    )
                except Exception as e:
                    self.log(f"Email notification failed: {e}")
                # ------------------------------

            except Exception as e:
                self.log(f"Error: {e}")

        threading.Thread(target=worker,daemon=True).start()

    # ---------- Undo ----------
    def undo_last(self):
        history = load_move_history()
        if not history: messagebox.showinfo("Undo","No history"); return
        last = history.pop()
        moves = last.get("moves",[])
        for rec in reversed(moves):
            if rec.get("dry"): continue
            src,dst=Path(rec["src"]),Path(rec["dst"])
            if dst.exists(): shutil.move(str(dst), str(make_unique_path(src)))
        save_move_history(history)
        messagebox.showinfo("Undo",f"Restored {len(moves)} files")

    # ---------- Duplicates ----------
    def find_duplicates_gui(self):
        if not self.current_target: return
        recursive = self.recursive_var.get()
        self.log("Finding duplicates...")
        def worker():
            duplicates = find_duplicates(self.current_target,recursive)
            self.root.after(0,lambda:self.show_duplicates_window(duplicates))
        threading.Thread(target=worker,daemon=True).start()

    def show_duplicates_window(self, duplicates):
        if not duplicates:
            messagebox.showinfo("Duplicates","No duplicates found.")
            return
        win = tk.Toplevel(self.root)
        win.title("Duplicates")
        tree = ttk.Treeview(win, columns=("group","path"),show="headings")
        tree.heading("group",text="Group")
        tree.heading("path",text="File path")
        tree.pack(fill=tk.BOTH,expand=True)
        tree.tag_configure("oddrow", background="#f0f8ff")
        tree.tag_configure("evenrow", background="white")
        for i,(h,paths) in enumerate(duplicates.items()):
            for p in paths:
                tag = "evenrow" if i %2==0 else "oddrow"
                tree.insert("",tk.END,values=(h[:10]+"...",p), tags=(tag,))
            i+=1

    # ---------- Dashboard ----------
    def view_dashboard_gui(self):
        if not self.current_target: return
        stats = gather_stats_for_dashboard(self.current_target,self.ext_map,self.recursive_var.get())
        self.show_dashboard_window(self.current_target,stats)

    def show_dashboard_window(self,target_path,stats):
        if not MATPLOTLIB_AVAILABLE:
            messagebox.showwarning("matplotlib missing","Install matplotlib for charts")
            return
        import numpy as np
        win = tk.Toplevel(self.root)
        win.title(f"Dashboard - {target_path}")
        win.geometry("1000x900")
        win.configure(bg="#f5f5f5")

        header = ttk.Frame(win)
        header.pack(fill=tk.X,padx=8,pady=6)
        ttk.Label(header,text=f"📊 Dashboard — {target_path}",font=("Segoe UI",12,"bold")).pack(anchor=tk.W)
        ttk.Label(header,text=f"Total files: {stats['total_files']}    Total size: {stats['total_size_mb']} MB").pack(anchor=tk.W)

        counts = stats["counts"]
        categories = list(counts.keys())
        history = load_move_history()
        monthly_counts = {}
        for run in history:
            try:
                t=datetime.fromisoformat(run["run_time"])
                key=f"{t.year}-{t.month:02d}"
                monthly_counts[key]=monthly_counts.get(key,0)+len(run.get("moves",[]))
            except: continue
        months = sorted(monthly_counts.keys())
        month_values = [monthly_counts[m] for m in months]

        all_files = list_files(target_path,recursive=True)
        size_info=[]
        for f in all_files:
            try: size_info.append((f.name,f.stat().st_size/1024/1024))
            except: pass
        size_info.sort(key=lambda x:x[1],reverse=True)
        top10=size_info[:10]
        top_names=[n for n,_ in top10]; top_sizes=[s for _,s in top10]

        fig,axs=plt.subplots(2,2,figsize=(8,6),dpi=100); fig.subplots_adjust(hspace=0.3,wspace=0.2)
        colors = plt.cm.Set3.colors

        pie_labels=[c for c,v in counts.items() if v>0]
        pie_values=[v for v in counts.values() if v>0]
        if pie_values: axs[0,0].pie(pie_values,labels=pie_labels,autopct="%1.1f%%",startangle=90, colors=colors)
        else: axs[0,0].text(0.5,0.5,"No files",ha="center",va="center")
        axs[0,0].set_title("Category Distribution")

        sizes=[]
        for cat in categories:
            s=sum(f.stat().st_size/1024/1024 for f in all_files if find_category_by_ext(self.ext_map,f.suffix)==cat)
            sizes.append(s)
        axs[0,1].bar(categories,sizes,color="#1f77b4")
        axs[0,1].set_title("Total Size (MB) by Category")
        axs[0,1].tick_params(axis='x',rotation=45)

        axs[1,0].plot(months,month_values,marker='o',linestyle='-',color='green')
        axs[1,0].set_title("Monthly Organized File Trend")
        axs[1,0].set_xlabel("Month"); axs[1,0].set_ylabel("Files moved")
        axs[1,0].tick_params(axis='x',rotation=45)

        axs[1,1].barh(top_names,top_sizes,color="#ff7f0e")
        axs[1,1].invert_yaxis()
        axs[1,1].set_title("Top 10 Largest Files (MB)")
        axs[1,1].set_xlabel("Size (MB)")

        canvas=FigureCanvasTkAgg(fig,master=win)
        canvas_widget=canvas.get_tk_widget()
        canvas_widget.pack(fill=tk.BOTH,expand=True,padx=8,pady=6)
        canvas.draw()

        ctrl=ttk.Frame(win); ctrl.pack(fill=tk.X,padx=8,pady=6)
        ttk.Button(ctrl,text="Refresh",command=self.view_dashboard_gui).pack(side=tk.LEFT,padx=6)
        ttk.Button(ctrl,text="Close",command=win.destroy).pack(side=tk.RIGHT,padx=6)

    # ---------- File Integrity Checker ----------
    def verify_file_integrity(self):
        from hashlib import sha256
        import json

        file_path = filedialog.askopenfilename(title="Select a file to verify")
        if not file_path:
            return

        hash_file = Path("file_hashes.json")

        def get_file_hash(path):
            hasher = sha256()
            with open(path, "rb") as f:
                while chunk := f.read(4096):
                    hasher.update(chunk)
            return hasher.hexdigest()

        # Load existing hashes
        if hash_file.exists():
            with open(hash_file, "r") as f:
                hashes = json.load(f)
        else:
            hashes = {}

        current_hash = get_file_hash(file_path)
        file_key = str(Path(file_path).resolve())

        if file_key in hashes:
            if hashes[file_key] == current_hash:
                messagebox.showinfo("File Integrity", "✅ File is unchanged and secure.")
            else:
                messagebox.showwarning("File Integrity", "⚠️ File has been modified or tampered!")
        else:
            hashes[file_key] = current_hash
            with open(hash_file, "w") as f:
                json.dump(hashes, f, indent=4)
            messagebox.showinfo("File Integrity", "🆕 File added as trusted baseline for future checks.")

    # ---------- Open log file ----------
    def open_log_file(self):
        log_path = Path(LOG_FILENAME).resolve()
        if log_path.exists():
            try:
                if sys.platform.startswith("win"):
                    os.startfile(str(log_path))
                elif sys.platform.startswith("darwin"):
                    os.system(f"open '{log_path}'")
                else:
                    os.system(f"xdg-open '{log_path}'")
            except Exception as e:
                messagebox.showerror("Error", f"Cannot open log file: {e}")
        else:
            messagebox.showinfo("Info", f"Log file not found: {log_path}")

    # ---------- Watchdog ----------
    def start_watchdog(self):
        if not self.current_target:
            messagebox.showwarning("Watchdog", "Select a target folder first!")
            return

        if not WATCHDOG_AVAILABLE:
            messagebox.showwarning("Watchdog", "watchdog module not installed.")
            return

        if self.observer:
            messagebox.showinfo("Watchdog", "Watchdog is already running.")
            return

        class Handler(FileSystemEventHandler):
            def __init__(self, gui):
                self.gui = gui
            def on_any_event(self, event):
                self.gui.task_queue.put(f"Detected: {event.event_type} - {event.src_path}")

        self.observer = Observer()
        self.observer.schedule(Handler(self), str(self.current_target), recursive=self.recursive_var.get())
        self.observer.start()
        self.log(f"Started watchdog on {self.current_target}")
        messagebox.showinfo("Watchdog", f"Started monitoring {self.current_target}")

    # ---------- Periodic UI ----------
    def _attach_periodic_check(self):
        def check_queue():
            try:
                while True:
                    msg=self.task_queue.get_nowait()
                    self.log(msg)
            except Empty:
                pass
            self.root.after(200,self._attach_periodic_check)
        self.root.after(200,check_queue)

    # ---------- Scheduler GUI ----------
    def schedule_organizer_gui(self):
        if not self.current_target:
            messagebox.showwarning("Scheduler","Select a target folder first!")
            return
        minutes = simpledialog.askinteger("Schedule Organizer", "Run every X minutes:", minvalue=1, maxvalue=1440)
        if minutes is None: return
        if self.scheduled_task:
            self.scheduled_task.cancel()
        self.log(f"Scheduled organizer every {minutes} minutes.")
        self._schedule_task(minutes*60)

    def _schedule_task(self, delay_seconds):
        def task():
            self.run_organize()
            self._schedule_task(delay_seconds)
        self.scheduled_task = threading.Timer(delay_seconds, task)
        self.scheduled_task.daemon = True
        self.scheduled_task.start()

# ---------- Run ----------
if __name__=="__main__":
    root = tk.Tk()
    app = OrganizerGUI(root)
    root.mainloop()
