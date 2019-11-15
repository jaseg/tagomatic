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
from PyQt5.QtGui import QPixmap, QValidator
from PyQt5.QtCore import QEvent, QItemSelectionModel, Qt

class TagValidator(QValidator):
    def __init__(self, db):
        super().__init__()
        self.db = db

    def validate(self, input, pos):
        if self.db.execute('SELECT id FROM tags WHERE name=?', (input,)).fetchone():
            return QValidator.Acceptable
        if self.db.execute('SELECT id FROM tags WHERE name LIKE ? + "%"', (input,)).fetchone():
            return QValidator.Intermediate
        return QValidator.Invalid

class App(QWidget):
    def __init__(self, db, img_path):
        super().__init__()
        self.setWindowTitle('Kochbuch Tag-O-Matic by jaseg')
        self.resize(1200, 1200)
        self.db = db
        self.img_path = img_path
        self.tag_validator = TagValidator(self.db)
        self.tag_box = None
        self.pixmap = None
        self.zoom_levels = [ a*b for a in [0.01, 0.1, 1, 10, 100] for b in [0.25, 0.5, 1.0] ]

        pics = self.db.execute('SELECT filename, id FROM pics WHERE valid=1 ORDER BY filename').fetchall()
        self.pid_for_fn = { k: v for k, v in pics }
        self.fn_for_pid = { v: k for k, v in pics }
        _, self.current_pic = pics[0]

        pic_scroll = QScrollArea()
        self.pic_label = QLabel(self)
        pic_scroll.setWidget(self.pic_label)
        pic_scroll.setWidgetResizable(True)
        self.title_edit = QLineEdit()

        self.pic_layout = QVBoxLayout()
        self.pic_layout.addWidget(pic_scroll)

        self.refresh_pic()

        self.pic_list = QListWidget()
        self.pic_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.pic_list.itemActivated.connect(lambda item: self.set_pic(self.pid_for_fn[item.text()]))
        for fn, _ in pics:
            self.pic_list.addItem(fn)
        self.pic_list.setCurrentRow(0, QItemSelectionModel.SelectCurrent)

        main_layout = QHBoxLayout()
        main_layout.addWidget(self.pic_list)
        main_layout.addLayout(self.pic_layout)
        self.setLayout(main_layout)
        QApplication.instance().focusChanged.connect(self.focusChanged)
        self.show()

    def set_pic(self, pid):
        self.current_pic = pid
        self.refresh_pic()

    def refresh_list(self):
        for i in range(self.pic_list.count()):
            item = self.pic_list.itemAt(i)
            pid = self.pid_for_fn[item.text()]

    def tags(self):
        return { name: (type, desc) for name, type, desc in
                self.db.execute('''SELECT name, type, description FROM tags''').fetchall() }

    def focusChanged(self, _old, new):
        if new in self.tag_edits:
            print(f'Changed focus to tag {self.tag_edits[new]}')
            tid, name = self.tag_edits[new]
            self.focused_tag_name = name

    def remove_tag(self, tid):
        self.db.execute('DELETE FROM pic_tags WHERE id=?', (tid,))
        self.refresh_pic()

    def update_tag(self, edit, tid):
        self.db.execute('UPDATE pic_tags SET value=? WHERE id=?', (edit.text(), tid))

    def add_tag(self):
        self.db.execute('INESRT INTO pic_tags (pic, tag) VALUES (?, ?)', (self.current_pic,))

    def set_zoom(self, zoom):
        self.zoom = zoom
        self.pic_label.setPixmap(self.pixmap.scaled(
            self.pixmap.width()*zoom, self.pixmap.height()*zoom, Qt.KeepAspectRatio)

    def change_zoom(self, delta):
        idx = max(0, min(len(self.zoom_levels)-1, self.zoom_levels.index(self.zoom) + delta))
        self.set_zoom(self.zoom_levels[idx])

    def refresh_pic(self):
        pid = self.current_pic
        le_path = path.join(
                self.img_path,
                *self.db.execute('SELECT path FROM pics WHERE id=?', (pid,)).fetchone(),
                self.fn_for_pid[pid])
        self.pixmap = QPixmap(le_path)
        print(path.isfile(le_path), le_path)
        self.set_zoom(self.zoom)

        tag_layout = QGridLayout()
        tag_layout.addWidget(QLabel('Title'), 0, 1)
        tag_layout.addWidget(self.title_edit, 0, 2)

        self.tag_edits = {}
        for i, (tid, name, value) in enumerate(self.db.execute('''SELECT pic_tags.id, tags.name, pic_tags.value FROM pic_tags
                JOIN tags ON pic_tags.tag = tags.id
                WHERE pic_tags.pic = ?''',
                (pid,)), start=1):
            remove_btn = QPushButton('Remove')
            remove_btn.clicked.connect(lambda: self.remove_tag(tid))
            tag_edit = QLineEdit()
            self.tag_edits[tag_edit] = tid, name
            tag_edit.editingFinished.connect(lambda: self.update_tag(tag_edit, tid))
            tag_layout.addWidget(remove_btn, i, 0)
            tag_layout.addWidget(QLabel(f'{name}:'), i, 1)
            tag_layout.addWidget(tag_edit, i, 2)

        add_button = QPushButton('Add')
        add_button.clicked.connect(self.add_tag)
        self.new_edit = QLineEdit()
        tag_layout.addWidget(QLabel('New:'), i+1, 0)
        tag_layout.addWidget(self.new_edit, i+1, 1)
        tag_layout.addWidget(add_button, i+1, 2)
        self.new_edit.returnPressed.connect(self.add_tag)
        self.new_edit.setValidator(self.tag_validator)

        if self.tag_box:
            self.pic_layout.removeWidget(self.tag_box)
        self.tag_box = QGroupBox('Image tags')
        self.tag_box.setLayout(tag_layout)
        self.pic_layout.addWidget(self.tag_box)
        self.update()

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('-d', '--database', default='kochbuch.sqlite3', help='Metadata db path, default: kochbuch.sqlite3')
    parser.add_argument('img_path', default='.', nargs='?', help='Base directory of image files')
    parser.add_argument('-r', '--reindex', action='store_true', help='reindex image files')
    args, unparsed = parser.parse_known_args()


    db = sqlite3.connect(args.database)
    db.execute('PRAGMA foreign_keys = ON')
    print(f'SQLite version {sqlite3.version}')

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
                    VALUES ("str", "dir", "Scan Directory")
                    ON CONFLICT DO NOTHING''')
            cur.execute('''INSERT INTO tags (type, name, description)
                    VALUES ("str", "title", "Image Title")
                    ON CONFLICT DO NOTHING''')

            print('Enumerating pages')
            piclist = glob.glob(path.join(glob.escape(args.img_path), '**/*.jpg'), recursive=True)
            print('   ', len(piclist), path.join(glob.escape(args.img_path), '**/*.jpg'))

            print('Reading pages')
            cur.execute('UPDATE pics SET valid=0')
            cur.execute('DELETE FROM pic_tags WHERE tag=(SELECT id FROM tags WHERE name="dir")')

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

                # upsert hack
                cur.execute('UPDATE pics SET filename=?, path=?, prefix=?, valid=1 WHERE sha3=?',
                        (fn, rel, prefix, hash))
                if cur.rowcount == 0:
                    cur.execute('INSERT INTO pics (filename, path, prefix, sha3, valid) VALUES (?, ?, ?, ?, 1)',
                    (fn, rel, prefix, hash))

                for tag in path.normpath(rel).split(os.sep):
                    cur.execute('''INSERT INTO pic_tags (pic, tag, value) VALUES (
                                    (SELECT id FROM pics WHERE sha3=?),
                                    (SELECT id FROM tags WHERE name="dir"),
                                    ?)''',
                            (hash, tag))

            print('Reindexing pages')
            for entries in prefixes.values():
                for num, (_1, _2, hash) in enumerate(sorted(entries, key=lambda tup: (tup[0], tup[1])), start=1):
                    cur.execute('UPDATE pics SET pgnum = ? WHERE sha3 = ?', (num, hash))

            print('Done.')

        for fn, hash in db.execute('SELECT filename, sha3 FROM pics WHERE valid=0').fetchall():
            print(f'Image file for entry {fn} ({hash}) disappeared.')


    signal.signal(signal.SIGINT, signal.SIG_DFL)
    app = QApplication(sys.argv[:1] + unparsed)
    ex = App(db, args.img_path)
    sys.exit(app.exec_())
