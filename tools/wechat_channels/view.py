"""Desktop presentation; all downloads and process access stay in the controller."""
from __future__ import annotations

import math
import tkinter as tk
from tkinter import ttk

BG = '#F3F6F5'
WHITE = '#FFFFFF'
INK = '#1B3029'
MUTED = '#74827C'
LINE = '#E2E9E5'
GREEN = '#09835D'


def duration_text(value):
    try:
        seconds = float(value)
        if not math.isfinite(seconds) or seconds < 0:
            return '—'
        seconds = int(seconds)
    except (TypeError, ValueError, OverflowError):
        return '—'
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f'{hours}:{minutes:02}:{seconds:02}' if hours else f'{minutes}:{seconds:02}'


def build_view(app):
    from .cards import VideoCards, BACKGROUND
    root = app.root
    root.title('ClipGo · 视频号')
    width = min(1080, max(850, root.winfo_screenwidth() - 100))
    height = min(780, max(640, root.winfo_screenheight() - 120))
    root.geometry(f'{width}x{height}')
    root.minsize(820, 600)
    root.configure(background=BACKGROUND)
    root.option_add('*Font', ('Microsoft YaHei UI', 10))
    style = ttk.Style(root)
    style.theme_use('clam')
    style.configure('.', font=('Microsoft YaHei UI', 10), foreground=INK)
    style.configure('TFrame', background=BACKGROUND)
    style.configure('TLabel', background=BACKGROUND, foreground=INK)
    style.configure('Muted.TLabel', foreground='#8B9791', font=('Microsoft YaHei UI', 9))
    style.configure('TButton', background=WHITE, foreground=INK, bordercolor=LINE,
                    lightcolor=WHITE, darkcolor=WHITE, borderwidth=1, padding=(12, 8))
    style.map('TButton', background=[('active', '#EDF4F0'), ('disabled', '#F4F6F5')], foreground=[('disabled', '#A8B3AD')])
    style.configure('Primary.TButton', background=GREEN, foreground=WHITE,
                    bordercolor=GREEN, lightcolor=GREEN, darkcolor=GREEN, font=('Microsoft YaHei UI', 9, 'bold'))
    style.map('Primary.TButton', background=[('disabled', '#DDE9E3'), ('active', '#076C4D')],
              foreground=[('disabled', '#8CA69A'), ('!disabled', WHITE)], bordercolor=[('disabled', '#DDE9E3'), ('!disabled', GREEN)])
    style.configure('Quiet.TButton', borderwidth=0, background=BACKGROUND, padding=(8, 6), font=('Microsoft YaHei UI', 9))
    style.configure('TMenubutton', background=BACKGROUND, borderwidth=0, padding=(8, 6), foreground=MUTED)
    style.configure('TEntry', fieldbackground=WHITE, bordercolor=LINE, padding=9)
    style.configure('TCombobox', fieldbackground=BACKGROUND, background=BACKGROUND, bordercolor=BACKGROUND, arrowcolor=MUTED, padding=8)
    style.map('TCombobox', fieldbackground=[('readonly', BACKGROUND)], selectbackground=[('readonly', BACKGROUND)], selectforeground=[('readonly', INK)])
    style.configure('Horizontal.TProgressbar', background=GREEN, troughcolor='#E4ECE7', borderwidth=0, lightcolor=GREEN, darkcolor=GREEN, thickness=5)
    style.configure('Vertical.TScrollbar', background='#DDE5E0', troughcolor=BACKGROUND, borderwidth=0, arrowsize=10)
    outer = ttk.Frame(root, padding=(26, 24, 20, 18))
    outer.pack(fill='both', expand=True)
    outer.columnconfigure(0, weight=1)
    outer.rowconfigure(2, weight=1)
    header = ttk.Frame(outer)
    header.grid(row=0, column=0, sticky='ew', pady=(0, 22))
    ttk.Label(header, text='ClipGo', font=('Segoe UI', 24, 'bold')).pack(side='left')
    ttk.Label(header, text='视频号', foreground='#8B9791', font=('Microsoft YaHei UI', 11)).pack(side='left', padx=(14, 0), pady=(9, 0))
    app.scan_button = ttk.Button(header, text='刷新视频', style='Primary.TButton', command=app.scan)
    app.scan_button.pack(side='right', padx=(10, 0))
    app.source_picker = ttk.Combobox(header, textvariable=app.source, values=('独立播放窗口', '微信主界面'), state='readonly', width=15)
    app.source_picker.pack(side='right')
    app.source_picker.bind('<<ComboboxSelected>>', app.source_changed)
    filterbar = ttk.Frame(outer)
    filterbar.grid(row=1, column=0, sticky='ew', pady=(0, 16))
    counts = ttk.Frame(filterbar)
    counts.pack(side='left')
    ttk.Label(counts, textvariable=app.list_count, font=('Microsoft YaHei UI', 10, 'bold')).pack(side='left')
    app.show_all_button = ttk.Button(counts, text='显示全部', style='Quiet.TButton', command=app.toggle_all)
    app.show_all_button.pack(side='left', padx=(12, 0))
    app.scan_indicator = ttk.Progressbar(counts, mode='indeterminate', length=45)
    searchbox = ttk.Frame(filterbar)
    searchbox.pack(side='right')
    app.search_entry = ttk.Entry(searchbox, textvariable=app.search, width=27)
    app.search_entry.pack()
    placeholder = tk.Label(app.search_entry, text='搜索视频标题或作者', foreground='#A0AAA4', background=WHITE,
                           font=('Microsoft YaHei UI', 9), cursor='xterm')
    placeholder.place(x=10, rely=.5, anchor='w')
    placeholder.bind('<Button-1>', lambda _: app.search_entry.focus_set())
    def search_changed(*_):
        placeholder.place_forget() if app.search.get() else placeholder.place(x=10, rely=.5, anchor='w')
        app.render_candidates()
    app.search.trace_add('write', search_changed)
    app.tree = VideoCards(outer, app)
    app.tree.grid(row=2, column=0, sticky='nsew')
    app.tree.bind('<<TreeviewSelect>>', app.selection_changed)
    app.empty_panel = ttk.Frame(app.tree, padding=24)
    ttk.Label(app.empty_panel, textvariable=app.empty_title, font=('Microsoft YaHei UI', 14, 'bold')).pack(pady=(0, 12))
    ttk.Label(app.empty_panel, textvariable=app.empty_hint, style='Muted.TLabel', justify='center').pack()
    app.empty_panel.place(relx=.5, rely=.45, anchor='center')
    app.transfer_panel = ttk.Frame(outer, padding=(0, 12))
    app.transfer_panel.grid(row=3, column=0, sticky='ew')
    app.transfer_panel.columnconfigure(1, weight=1)
    ttk.Label(app.transfer_panel, textvariable=app.transfer_state, foreground=GREEN,
              font=('Microsoft YaHei UI', 10, 'bold')).grid(row=0, column=0, sticky='w', padx=(0, 12))
    ttk.Label(app.transfer_panel, textvariable=app.task_title, width=1, style='Muted.TLabel').grid(row=0, column=1, sticky='ew')
    app.cancel_button = ttk.Button(app.transfer_panel, text='取消', style='Quiet.TButton', command=app.cancel_download, state='disabled')
    app.cancel_button.grid(row=0, column=2, rowspan=2, padx=(12, 0))
    app.progress = ttk.Progressbar(app.transfer_panel, maximum=100)
    app.progress.grid(row=1, column=0, columnspan=2, sticky='ew', pady=(9, 5))
    ttk.Label(app.transfer_panel, textvariable=app.detail, style='Muted.TLabel', wraplength=900).grid(row=2, column=0, columnspan=3, sticky='w')
    app.transfer_panel.grid_remove()
    footer = ttk.Frame(outer)
    footer.grid(row=4, column=0, sticky='ew', pady=(14, 0))
    footer.columnconfigure(1, weight=1)
    ttk.Label(footer, textvariable=app.status, style='Muted.TLabel').grid(row=0, column=0, columnspan=5, sticky='w', pady=(0, 8))
    ttk.Label(footer, text='保存到', style='Muted.TLabel').grid(row=1, column=0, padx=(0, 10))
    ttk.Label(footer, textvariable=app.folder, width=1, style='Muted.TLabel').grid(row=1, column=1, sticky='ew')
    ttk.Button(footer, text='更改', command=app.choose_folder, style='Quiet.TButton').grid(row=1, column=2)
    ttk.Button(footer, text='打开文件夹', command=app.open_folder, style='Quiet.TButton').grid(row=1, column=3, padx=(6, 0))
    more = ttk.Menubutton(footer, text='···')
    more.grid(row=1, column=4, padx=(4, 0))
    menu = tk.Menu(more, tearoff=False)
    menu.add_command(label='实验网络监听（本机不推荐）', command=app.start)
    menu.add_command(label='停止网络监听', command=app.stop)
    menu.add_command(label='清理临时证书', command=app.cleanup)
    more['menu'] = menu
    app.start_button = ttk.Button(footer, command=app.start)
    app.stop_button = ttk.Button(footer, command=app.stop, state='disabled')
    app.selection_changed()
