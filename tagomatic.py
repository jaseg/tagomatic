#!/usr/bin/env python3

import sys
import os
import os.path as path
import signal
import glob
import re
import collections
import hashlib
import sqlite3

import tqdm

from PyQt5.QtWidgets import *
from PyQt5.QtGui import QPixmap, QValidator, QTransform, QKeySequence
from PyQt5.QtCore import Qt, QEvent, QItemSelectionModel, QCoreApplication, QSettings, QTimer

class TagValidator(QValidator):
    def __init__(self, db):
        super().__init__()
        self.db = db

    def validate(self, input, pos):
        if self.db.execute('SELECT id FROM tags WHERE name=? LIMIT 1', (input,)).fetchone():
            return QValidator.Acceptable, input, pos
        return QValidator.Intermediate, input, pos

class ZoomView(QGraphicsView):
    def __init__(self, zoom=1.0, zoom_cb=lambda zoom: None):
        super().__init__()
        self.pixmap = None
        self.zoom = zoom
        self.zoom_cb = zoom_cb
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.pixmap_item = QGraphicsPixmapItem()
        self.scene = QGraphicsScene()
        self.scene.addItem(self.pixmap_item)
        self.setScene(self.scene)
        self.setTransform(QTransform.fromScale(zoom, zoom))

    def set_pic(self, le_path):
        self.pixmap = QPixmap(le_path)
        self.pixmap_item.setPixmap(self.pixmap)

    def set_zoom(self, zoom):
        self.zoom = zoom
        self.setTransform(QTransform.fromScale(zoom, zoom))
        self.zoom_cb(zoom)

    def wheelEvent(self, evt):
        if evt.modifiers() == Qt.ControlModifier:
            self.set_zoom(max(1/16, min(4, self.zoom * (1.2**(evt.angleDelta().y() / 120)))))
        elif evt.modifiers() == Qt.ShiftModifier:
            QCoreApplication.sendEvent(self.horizontalScrollBar(), evt)
        else:
            if abs(evt.angleDelta().x()) > abs(evt.angleDelta().y()):
                QCoreApplication.sendEvent(self.horizontalScrollBar(), evt)
            else:
                QCoreApplication.sendEvent(self.verticalScrollBar(), evt)

class App(QWidget):
    def __init__(self, db, img_path):
        super().__init__()
        self.setWindowTitle('Kochbuch Tag-O-Matic by jaseg')
        self.resize(1200, 1200)
        self.db = db
        self.img_path = img_path
        self.tag_validator = TagValidator(self.db)
        self.tag_box = None
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self.refresh_pic)
        self.refresh_timer.setSingleShot(True)
        self.refresh_timer.setInterval(0)
        self.tag_edits = {}
        self.focused_tag_tid = None
        self.settings = QSettings()

        tags = [ tag for tag, in self.db.execute('SELECT name FROM tags') ]
        self.new_completer = QCompleter(tags, self)
        self.new_completer.setCompletionMode(QCompleter.InlineCompletion)
        self.new_completer.setCaseSensitivity(Qt.CaseInsensitive)

        pics = self.db.execute('SELECT filename, id FROM pics WHERE valid=1 ORDER BY filename').fetchall()
        self.pid_for_fn = { k: v for k, v in pics }
        self.fn_for_pid = { v: k for k, v in pics }
        _, first_pid = pics[0]
        self.current_pic = int(self.settings.value("current_picture", first_pid))
        #self.shown = list(self.fn_for_pid.keys())

        zoom = float(self.settings.value('zoom', 1.0))
        def save_zoom(zoom):
            self.settings.setValue('zoom', zoom)
        self.zoom_view = ZoomView(zoom, save_zoom)

        self.pic_layout = QVBoxLayout()
        self.pic_layout.addWidget(self.zoom_view)

        self.refresh_pic()

        self.pic_list = QListWidget()
        self.pic_list.setSelectionMode(QAbstractItemView.SingleSelection)
        show_item = lambda item: self.set_pic(self.pid_for_fn[item.text()])
        self.pic_list.itemActivated.connect(show_item)
        self.pic_list.itemClicked.connect(show_item)
        for fn, _ in pics:
            self.pic_list.addItem(fn)
        # Use row-based indexing since at this point there's no filters yet.
        idx = next(i for i, (_fn, pid) in enumerate(pics) if pid == self.current_pic)
        self.pic_list.setCurrentRow(idx, QItemSelectionModel.SelectCurrent)

        nav_prev = QPushButton('Previous')
        nav_prev.clicked.connect(self.prev)
        nav_next = QPushButton('Next')
        nav_next.clicked.connect(self.next)

        shortcut1 = QShortcut(QKeySequence('Ctrl+N'), self)
        shortcut1.activated.connect(self.next)
        shortcut2 = QShortcut(QKeySequence('Ctrl+P'), self)
        shortcut2.activated.connect(self.prev)
        shortcut3 = QShortcut(QKeySequence(QKeySequence.MoveToNextPage), self)
        shortcut3.activated.connect(self.next)
        shortcut4 = QShortcut(QKeySequence(QKeySequence.MoveToPreviousPage), self)
        shortcut4.activated.connect(self.prev)
        shortcut4 = QShortcut(QKeySequence(Qt.CTRL + Qt.Key_Minus), self)
        shortcut4.activated.connect(self.remove_current_tag)
        #shortcut5 = QShortcut(QKeySequence(Qt.CTRL+ Qt.Key_Return), self)
        #shortcut5.activated.connect(self.next)

        nav_buttons = QHBoxLayout()
        nav_buttons.addWidget(nav_prev)
        nav_buttons.addWidget(nav_next)
        list_layout = QVBoxLayout()
        list_layout.addWidget(self.pic_list)
        list_layout.addLayout(nav_buttons)
        main_layout = QHBoxLayout()
        main_layout.addLayout(list_layout)
        main_layout.addLayout(self.pic_layout)
        self.setLayout(main_layout)
        QApplication.instance().focusChanged.connect(self.focusChanged)
        self.show()

    def navigate(self, movement):
        idx = self.pic_list.moveCursor(movement, Qt.NoModifier)
        cmd = self.pic_list.selectionCommand(idx, None)
        self.pic_list.selectionModel().setCurrentIndex(idx, cmd)
        items = self.pic_list.selectedItems()
        if len(items) != 1:
            return
        item, = items
        self.set_pic(self.pid_for_fn[item.text()])

    def prev(self):
        self.navigate(QAbstractItemView.MovePrevious)

    def next(self):
        self.navigate(QAbstractItemView.MoveDown)

    def schedule_refresh(self):
        self.refresh_timer.start()

    def set_pic(self, pid):
        self.current_pic = pid
        self.settings.setValue("current_picture", pid)
        self.schedule_refresh()

    def refresh_list(self):
        for i in range(self.pic_list.count()):
            item = self.pic_list.itemAt(i)
            pid = self.pid_for_fn[item.text()]

    def tags(self):
        return { name: (type, desc) for name, type, desc in
                self.db.execute('''SELECT name, type, description FROM tags''').fetchall() }

    def focusChanged(self, _old, new):
        if new in self.tag_edits:
            tid, _lid = self.tag_edits[new]
            self.focused_tag_tid = tid

    def remove_current_tag(self):
        obj = QApplication.instance().focusWidget()
        if obj in self.tag_edits:
            _tid, lid = self.tag_edits[obj]
            self.remove_tag(lid)

    def remove_tag(self, lid):
        with self.db:
            self.db.execute('DELETE FROM pic_tags WHERE id=?', (lid,))
        self.refresh_pic()

    def update_tag(self, edit, lid):
        with self.db:
            self.db.execute('UPDATE pic_tags SET value=? WHERE id=?', (edit.text(), lid))

    def add_tag(self):
        if not self.new_edit.hasAcceptableInput():
            return
        name = self.new_edit.text()
        with self.db:
            tid, = self.db.execute('SELECT id FROM tags WHERE name=?', (name,)).fetchone()
            self.db.execute('INSERT INTO pic_tags (pic, tag) VALUES (?, ?)', (self.current_pic, tid))
            self.focused_tag_tid = tid
        self.refresh_pic()

    def refresh_pic(self):
        pid = self.current_pic
        le_path = path.join(
                self.img_path,
                *self.db.execute('SELECT path FROM pics WHERE id=?', (pid,)).fetchone(),
                self.fn_for_pid[pid])
        self.zoom_view.set_pic(le_path)

        tag_layout = QGridLayout()

        self.tag_edits = {}
        tag_edit_for_tid = {}
        for i, (lid, tid, tag_type, name, desc, value) in enumerate(self.db.execute(
                '''SELECT pic_tags.id, tags.id, tags.type, name, description, value
                FROM pic_tags JOIN tags ON pic_tags.tag = tags.id
                WHERE pic_tags.pic = ?''',
                (pid,)), start=1):
            is_bool = tag_type == 'bool'
            remove_btn = QPushButton('Remove')
            remove_btn.clicked.connect(lambda: self.remove_tag(lid))
            tag_layout.addWidget(remove_btn, i, 0)
            tag_layout.addWidget(QLabel(f'{desc}:' if not is_bool else desc), i, 1)

            if not is_bool:
                tag_edit = QLineEdit()
                self.tag_edits[tag_edit] = tid, lid
                tag_edit_for_tid[tid] = tag_edit
                tag_edit.setText(value)
                tag_edit.editingFinished.connect(lambda: self.update_tag(tag_edit, lid))
                tag_edit.returnPressed.connect(self.next)
                tag_layout.addWidget(tag_edit, i, 2)

        add_button = QPushButton('Add')
        manage_button = QPushButton('Manage')
        add_button.setEnabled(False)
        add_button.clicked.connect(self.add_tag)
        self.new_edit = QLineEdit()
        self.new_edit.setCompleter(self.new_completer)
        def edited(text):
            ok = self.new_edit.hasAcceptableInput()
            add_button.setEnabled(ok)
            color = '#c4df9b' if ok else ('#fff79a' if text else '#ffffff')
            self.new_edit.setStyleSheet(f'QLineEdit {{ background-color: {color} }}')
        self.new_edit.textEdited.connect(edited)
        new_layout = QHBoxLayout()
        new_layout.addWidget(QLabel('New:'))
        new_layout.addWidget(self.new_edit)
        new_layout.addWidget(add_button)
        new_layout.addWidget(manage_button)
        self.new_edit.returnPressed.connect(self.add_tag)
        self.new_edit.setValidator(self.tag_validator)

        if self.tag_box:
            self.pic_layout.removeWidget(self.tag_box)
            self.tag_box.close()
        self.tag_box = QGroupBox('Image tags')
        self.tag_box.setAttribute(Qt.WA_DeleteOnClose, True)
        box_layout = QVBoxLayout()
        box_layout.addLayout(tag_layout)
        box_layout.addLayout(new_layout)
        self.tag_box.setLayout(box_layout)
        self.pic_layout.addWidget(self.tag_box)

        if self.focused_tag_tid in tag_edit_for_tid:
            tag_edit_for_tid[self.focused_tag_tid].setFocus()

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('-d', '--database', default='kochbuch.sqlite3', help='Metadata db path, default: kochbuch.sqlite3')
    parser.add_argument('img_path', default='.', nargs='?', help='Base directory of image files')
    parser.add_argument('-r', '--reindex', action='store_true', help='reindex image files')
    args, unparsed = parser.parse_known_args()


    db = sqlite3.connect(args.database)
    db.execute('PRAGMA foreign_keys = ON')

    db_initialized = bool(db.execute('SELECT name FROM sqlite_master WHERE type="table" AND name="pics"').fetchone())
    if not db_initialized:
        print('DB uninitialized')

    if args.reindex or not db_initialized:
        print('Reinitializing database')
        prefixes = collections.defaultdict(lambda: [])
        with db:
            cur = db.cursor()
            cur.execute('''CREATE TABLE IF NOT EXISTS pics (
                    id INTEGER PRIMARY KEY,
                    filename TEXT,
                    category TEXT,
                    book TEXT,
                    prefix TEXT,
                    sha3 TEXT UNIQUE,
                    pgnum INTEGER,
                    path TEXT,
                    valid INTEGER)''')

            cur.execute('''CREATE TABLE IF NOT EXISTS tags (
                    id INTEGER PRIMARY KEY,
                    type TEXT,
                    name TEXT UNIQUE COLLATE NOCASE,
                    description TEXT)''')

            cur.execute('''CREATE TABLE IF NOT EXISTS pic_tags (
                    id INTEGER PRIMARY KEY,
                    pic REFERENCES pics(id) ON DELETE CASCADE ON UPDATE CASCADE,
                    tag REFERENCES tags(id) ON DELETE CASCADE ON UPDATE CASCADE,
                    value TEXT)''')

            cur.execute('''INSERT INTO tags (type, name, description)
                    VALUES ("str", "title", "Image Title"),
                           ("str", "comment", "Comment"),
                           ("bool", "check", "Double-check this image")
                    ON CONFLICT DO NOTHING''')

            print('Enumerating pages')
            piclist = glob.glob(path.join(glob.escape(args.img_path), '**/*.jpg'), recursive=True)

            print('Reading pages')
            cur.execute('UPDATE pics SET valid=0')

            for pic in tqdm.tqdm(piclist):
                relpath = path.relpath(pic, args.img_path)
                rel, fn = path.split(relpath)

                m = re.match(r'(.*?)([0-9]+)-([0-9]+)\.jpg', fn)
                if not m:
                    raise UserWarning(f"Can't parse image filename \"{pic}\"")

                prefix, series, imgnum = m.groups()

                with open(pic, 'rb') as f:
                    hash = hashlib.sha3_256()
                    hash.update(f.read())
                    hash = hash.hexdigest()

                prefixes[prefix].append((int(series), int(imgnum), hash))

                book, *rest = path.normpath(rel).split(os.sep)
                if rest:
                    category, *rest = rest
                    if rest:
                        raise UserWarning(f'Unhandled directory component "{rest}"')
                else:
                    category = None

                # upsert hack
                cur.execute('UPDATE pics SET book=?, category=?, filename=?, path=?, prefix=?, valid=1 WHERE sha3=?',
                        (book, category, fn, rel, prefix, hash))
                if cur.rowcount == 0:
                    cur.execute('INSERT INTO pics (book, category, filename, path, prefix, sha3, valid) VALUES (?, ?, ?, ?, ?, ?, 1)',
                    (book, category, fn, rel, prefix, hash))

                title_tag_exists = bool(db.execute('''SELECT pic_tags.id FROM pic_tags
                        JOIN pics ON pics.id=pic_tags.pic, tags ON tags.id=pic_tags.tag
                        WHERE tags.name="title" AND pics.sha3=?''', (hash,)).fetchone())

                if not title_tag_exists:
                    db.execute('''INSERT INTO pic_tags (pic, tag) VALUES (
                            (SELECT id FROM pics WHERE sha3=?),
                            (SELECT id FROM tags WHERE name="title"))''',
                            (hash,))

            print('Reindexing pages')
            for entries in prefixes.values():
                for num, (_1, _2, hash) in enumerate(sorted(entries, key=lambda tup: (tup[0], tup[1])), start=1):
                    cur.execute('UPDATE pics SET pgnum = ? WHERE sha3 = ?', (num, hash))

            print('Done.')

        for fn, hash in db.execute('SELECT filename, sha3 FROM pics WHERE valid=0').fetchall():
            print(f'Image file for entry {fn} ({hash}) disappeared.')


    signal.signal(signal.SIGINT, signal.SIG_DFL)
    QCoreApplication.setOrganizationName('jaseg') 
    QCoreApplication.setOrganizationDomain('jaseg.net') 
    QCoreApplication.setApplicationName('Tag-O-Matic') 
    app = QApplication(sys.argv[:1] + unparsed)
    ex = App(db, args.img_path)
    sys.exit(app.exec_())
