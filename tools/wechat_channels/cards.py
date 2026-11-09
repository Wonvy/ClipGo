"""Small responsive video cards for the local Tk desktop."""
from __future__ import annotations

from collections import OrderedDict
import tkinter as tk
from tkinter import ttk, font as tkfont
from PIL import Image, ImageOps, ImageDraw, ImageTk

BACKGROUND = '#F5F7F8'
MAX_IMAGE_ITEMS = 64


def excerpt(text, width, font, limit=3):
    text = ' '.join(str(text).split())
    lines, line = [], ''
    for index, char in enumerate(text):
        if line and font.measure(line + char) > width:
            lines.append(line)
            line = ''
            if len(lines) == limit:
                last = lines[-1]
                while last and font.measure(last + '…') > width:
                    last = last[:-1]
                return '\n'.join(lines[:-1] + [last + '…'])
        line += char
    if line:
        lines.append(line)
    return '\n'.join(lines)


class VideoCards(ttk.Frame):
    """Selection/list interface shared with the controller, rendered as cards."""
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.records = {}
        self.widgets = {}
        self.selected = ()
        self.photos = {}
        self.images = OrderedDict()
        self.failed_images = set()
        self.heading_font = tkfont.Font(family='Microsoft YaHei UI', size=11, weight='bold')
        self.canvas = tk.Canvas(self, bg=BACKGROUND, highlightthickness=0, borderwidth=0)
        scrollbar = ttk.Scrollbar(self, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.pack(side='left', fill='both', expand=True)
        scrollbar.pack(side='right', fill='y')
        self.inner = tk.Frame(self.canvas, bg=BACKGROUND)
        self.inner_window = self.canvas.create_window((0, 0), window=self.inner, anchor='nw')
        self.canvas.bind('<Configure>', self._resize)
        self.inner.bind('<Configure>', lambda _: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self._redraw_pending = None
        self._last_width = 0
        self._wheel_remainder = 0
        self.canvas.bind('<Up>', lambda _: self._navigate(-1))
        self.canvas.bind('<Down>', lambda _: self._navigate(1))
        self.canvas.bind('<Return>', lambda _: self._activate_selected())
        self.canvas.bind('<MouseWheel>', self._wheel)
        self.inner.bind('<MouseWheel>', self._wheel)

    def _wheel(self, event):
        if self.inner.winfo_reqheight() > self.canvas.winfo_height():
            self._wheel_remainder += event.delta
            steps = int(self._wheel_remainder / 120)
            if steps:
                self._wheel_remainder -= steps * 120
                self.canvas.yview_scroll(-steps, 'units')
        return 'break'

    def _navigate(self, direction):
        keys = tuple(self.records)
        if keys:
            index = keys.index(self.selected[0]) if self.selected and self.selected[0] in keys else 0
            self.selection_set(keys[max(0, min(len(keys) - 1, index + direction))])
            self.see(self.selected[0])
        return 'break'

    def _activate_selected(self):
        if self.selected:
            self.app.card_action(self.selected[0])
        return 'break'

    def _select_from_pointer(self, ident):
        self.selection_set(ident)
        self.canvas.focus_set()

    def _resize(self, event):
        self.canvas.itemconfigure(self.inner_window, width=event.width)
        if abs(event.width - self._last_width) > 2:
            self._last_width = event.width
            self._schedule()

    def _schedule(self):
        if self._redraw_pending:
            self.after_cancel(self._redraw_pending)
        self._redraw_pending = self.after_idle(self._draw)

    def get_children(self):
        return tuple(self.records)

    def delete(self, *idents):
        for ident in idents:
            self.records.pop(ident, None)
        self.selected = tuple(i for i in self.selected if i in self.records)
        self._schedule()

    def insert(self, _parent, _where, *, iid, values, tags=()):
        self.records[iid] = tuple(values)
        self._schedule()

    def exists(self, ident):
        return ident in self.records

    def item(self, ident, option=None, **kwargs):
        if 'values' in kwargs:
            self.records[ident] = tuple(kwargs['values'])
            self._schedule()
        return self.records[ident] if option == 'values' else {'values': self.records[ident]}

    def selection(self):
        return self.selected

    def selection_set(self, ident):
        self.selected = (ident,) if ident in self.records else ()
        for key, parts in self.widgets.items():
            parts['border'].configure(highlightbackground='#7BB79E' if key == ident else '#E4E9E7')
        self.event_generate('<<TreeviewSelect>>')

    def focus(self, _ident=None):
        # Match Treeview.focus(item): change the item focus, not keyboard focus.
        # Search-triggered renders must not steal typing from the search entry.
        if _ident is not None:
            self._focus_item = _ident if _ident in self.records else ''
        return getattr(self, '_focus_item', '')

    def see(self, ident):
        if ident not in self.records:
            return
        if self._redraw_pending:
            self.after_idle(lambda key=ident: self.see(key))
            return
        parts = self.widgets.get(ident)
        if not parts:
            return
        total = max(1, self.inner.winfo_reqheight())
        viewport = self.canvas.winfo_height()
        if total <= viewport:
            return
        top = parts['border'].winfo_y()
        bottom = top + parts['border'].winfo_height()
        visible_top = self.canvas.canvasy(0)
        if top < visible_top:
            self.canvas.yview_moveto(max(0, top / total))
        elif bottom > visible_top + viewport:
            self.canvas.yview_moveto(min(1, max(0, (bottom - viewport) / total)))

    def _prune_images(self):
        active_urls = {entry.get('thumbnail_url') for entry in self.app.candidates.values()}
        for url in tuple(self.images):
            if url not in active_urls:
                self.images.pop(url, None)
        self.failed_images.intersection_update(active_urls)
        while len(self.images) > MAX_IMAGE_ITEMS:
            self.images.popitem(last=False)
        while len(self.failed_images) > MAX_IMAGE_ITEMS:
            self.failed_images.pop()

    def set_thumbnail(self, url, image):
        if not any(entry.get('thumbnail_url') == url for entry in self.app.candidates.values()):
            return
        if image is None:
            self.failed_images.add(url)
        else:
            self.images[url] = image
            self.images.move_to_end(url)
            self.failed_images.discard(url)
        self._prune_images()
        for ident, parts in self.widgets.items():
            if self.app.candidates.get(ident, {}).get('thumbnail_url') == url:
                self._photo(ident, parts)

    def _photo(self, ident, parts):
        url = self.app.candidates.get(ident, {}).get('thumbnail_url')
        label = parts['image']
        source = self.images.get(url)
        if source is not None:
            canvas = Image.new('RGB', (112, 154), '#EBEFED')
            image = ImageOps.contain(source.convert('RGB'), (112, 154), Image.Resampling.LANCZOS)
            canvas.paste(image, ((112 - image.width) // 2, (154 - image.height) // 2))
            mask = Image.new('L', canvas.size, 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, 111, 153), radius=8, fill=255)
            canvas.putalpha(mask)
            photo = ImageTk.PhotoImage(canvas, master=self)
            self.photos[ident] = photo
            label.configure(image=photo, text='', background='white')
        else:
            label.configure(image='', text='暂无封面' if not url or url in self.failed_images else '加载封面…',
                            foreground='#9BA8A1', background='#EBEFED')

    def update_actions(self):
        for ident, parts in self.widgets.items():
            saved = ident in self.app.saved_files
            button = parts['button']
            button.configure(text='打开视频' if saved else '下载视频',
                             state='normal' if saved or not self.app.active_download_id else 'disabled')
            entry = self.app.candidates.get(ident, {})
            text = self.app.download_states.get(ident, '')
            parts['state'].configure(text=text)

    def _draw(self):
        self._redraw_pending = None
        self._prune_images()
        for child in self.inner.winfo_children():
            child.destroy()
        self.widgets.clear()
        self.photos.clear()
        available = max(360, self.canvas.winfo_width() - 2)
        columns = 2 if available >= 800 else 1
        gap = 14
        width = (available - gap * (columns - 1)) // columns
        for index, (ident, values) in enumerate(self.records.items()):
            border = tk.Frame(self.inner, bg='white', width=width, height=188,
                highlightthickness=1, highlightbackground='#7BB79E' if ident in self.selected else '#E4E9E7')
            border.grid(row=index // columns, column=index % columns, sticky='nsew',
                        padx=(0, gap if index % columns < columns - 1 else 0), pady=(0, gap))
            border.grid_propagate(False)
            image_frame = tk.Frame(border, bg='white', width=112, height=154)
            image_frame.place(x=16, y=16, width=112, height=154)
            image_label = tk.Label(image_frame, bg='#EBEFED', font=('Microsoft YaHei UI', 9), borderwidth=0)
            image_label.place(x=0, y=0, width=112, height=154)
            duration = tk.Label(image_frame, text=values[2], bg='#213D32', fg='white',
                                font=('Segoe UI', 8, 'bold'), padx=5, pady=2)
            duration.place(relx=1, rely=1, x=-6, y=-6, anchor='se')
            content_width = max(180, width - 160)
            title = tk.Label(border, text=excerpt(values[0], content_width, self.heading_font),
                bg='white', fg='#21352C', font=self.heading_font, justify='left', anchor='nw', cursor='hand2')
            title.place(x=144, y=18, width=content_width, height=74)
            author = tk.Label(border, text=excerpt(values[1], content_width, tkfont.Font(family='Microsoft YaHei UI', size=9), 1),
                bg='white', fg='#84918A', font=('Microsoft YaHei UI', 9), anchor='w')
            author.place(x=144, y=101, width=content_width, height=20)
            state = tk.Label(border, bg='white', fg='#0A875C', font=('Microsoft YaHei UI', 9), anchor='w')
            state.place(x=144, y=143, width=max(70, content_width - 112), height=24)
            button = ttk.Button(border, text='下载视频', style='Primary.TButton',
                command=lambda key=ident: self.app.card_action(key))
            button.place(x=width - 112, y=133, width=96, height=36)
            self.widgets[ident] = {'border': border, 'image': image_label, 'button': button, 'state': state}
            for widget in (border, image_frame, image_label, author, duration):
                widget.bind('<Button-1>', lambda event, key=ident: self._select_from_pointer(key))
                widget.bind('<MouseWheel>', self._wheel)
            for widget in (button, state):
                widget.bind('<MouseWheel>', self._wheel)
            title.bind('<Button-1>', lambda event, key=ident: self.app.show_details(key))
            title.bind('<MouseWheel>', self._wheel)
            image_label.bind('<Double-Button-1>', lambda event, key=ident: self.app.show_details(key))
            self._photo(ident, self.widgets[ident])
        self.update_actions()
        self.inner.update_idletasks()
        self.canvas.configure(scrollregion=self.canvas.bbox('all'))
