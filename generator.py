#!/usr/bin/env python3

import subprocess
import tempfile
import hashlib
import glob
import shutil
import os
from os import path
from urllib import parse
import sqlite3

import tqdm
import jinja2

MAIN_IDX_TEMPL = jinja2.Template('''
<html>
<head>
    <meta charset="UTF-8">
    <title>{{ title }}</title>
    <link rel="stylesheet" type="text/css" href="style.css">
</head>
<body class="idx main">
    <h1>{{ title }}</h1>

    <a href="../{{ zip_name }}">Download Everything (zip)</a>,<br/>
    <a href="pages.html">Overall Page List</a>

    <h2>Books</h2>
    <ul>
        {% for book, idx, pages, cats in books %}
        <li>
            {{ book }}: 
            <a href="{{ idx }}">Index</a>,
            <a href="{{ pages }}">Pages</a>{{ "," if cats }}
            {% for cat, cat_fn in cats %}
                <a href="{{ cat_fn }}">{{ cat }}</a>{{ "," if not loop.last }}
            {% endfor %}
        </li>
        {% endfor %}
    </ul>

    <h2>Entries</h2>
    <ul class="entries">
        {% for title, path in entries %}
            <li><a href="{{ path }}">{{ title }}</a></li>
        {% endfor %}
    </ul>
</body>
</html>
''')

CAT_PAGE_LIST_TEMPL = jinja2.Template('''
<html>
<head>
    <meta charset="UTF-8">
    <title>{{ title }}: {{ cat }} in {{ book }}</title>
    <link rel="stylesheet" type="text/css" href="../style.css">
</head>
<body class="pglist book">
    <h1>{{ cat }} in {{ book }}</h1>
    <a href="../index.html">Home</a>
    <ol class="pglist">
    {% for pgnum, thumb, link in pages %}
        <li value="{{ pgnum }}"><a href="{{ link }}"><img src="{{ thumb }}" alt="Page {{ pgnum }}"/></a></li>
    {% endfor %}
    </ol>
</body>
</html>
''')

BOOK_PAGE_LIST_TEMPL = jinja2.Template('''
<html>
<head>
    <meta charset="UTF-8">
    <title>{{ title }}: Page listing of {{ book }}</title>
    <link rel="stylesheet" type="text/css" href="../style.css">
</head>
<body class="pglist book">
    <h1>Pages of {{ book }}</h1>
    <a href="../index.html">Home</a>
    <ol class="pglist">
    {% for pgnum, thumb, _img, link, _hash in pages %}
        <li value="{{ pgnum }}"><a href="{{ link }}"><img src="{{ thumb }}" alt="Page {{ pgnum }}"/></a></li>
    {% endfor %}
    </ol>
</body>
</html>
''')

MAIN_PAGE_LIST_TEMPL = jinja2.Template('''
<html>
<head>
    <meta charset="UTF-8">
    <title>{{ title }}</title>
    <link rel="stylesheet" type="text/css" href="style.css">
</head>
<body class="pglist main">
    <h1>{{ title }}: Overall Page Listing</h1>
    <a href="index.html">Home</a>
    <h2>Books</h2>
    <ul class="contents">
        {% for book, pages in books %}
        <li><a href="#pages-{{ parse.quote(book) }}">{{ book }}</a></li>
        {% endfor %}
    </ul>

    <h2>Pages</h2>
    {% for book, pages in books %}
        <h4 id="pages-{{ parse.quote(book) }}">{{ book }}</h4>
        <ol class="pglist">
        {% for pgnum, thumb, link in pages %}
            <li value="{{ pgnum }}"><a href="{{ link }}"><img src="{{ thumb }}" alt="Page {{ pgnum }}"/></a></li>
        {% endfor %}
        </ol>
    {% endfor %}
</body>
</html>
''')

BOOK_IDX_TEMPL = jinja2.Template('''
<html>
<head>
    <meta charset="UTF-8">
    <title>{{ title }}: Index of {{ book }}</title>
    <link rel="stylesheet" type="text/css" href="../style.css">
</head>
<body class="idx book">
    <h1>Index of {{ book }}</h1>
    <a href="../index.html">Home</a>
    <h2>Entries</h2>
    <ul class="entries">
    {% for title, path in entries %}
        <li><a href="{{ path }}">{{ title }}</a></li>
    {% endfor %}
    </ul>
</body>
</html>
''')

PAGE_TEMPL = jinja2.Template('''
<html>
<head>
    <meta charset="UTF-8">
    <title>{{ title }}: Page {{ pgnum }} of {{ book }}</title>
    <link rel="stylesheet" type="text/css" href="../../style.css">
</head>
<body class="page">
    <h1>{{ book }}: {{ pgnum }}{{ " ("+cat+")" if cat }}</h1>
    {% if titles %}
    <div>
    {% for title in titles %}
       {{ title }}{{ "," if not loop.last }}
    {% endfor %}
    </div>
    {% endif %}
    <div class="links"><a href="../../index.html">Home</a>, <a href="{{ page_list }}">Pages</a>, <a href="{{ index }}">Index</a></div>

    <div class="viewer">
        {% if has_prev %}
        <a class="nav prev" href="{{ prev_link }}" title="Previous page: {{ prev_num }}">
            <img src="{{ prev_img }}" alt="Page {{ prev_num }} of {{ book }}">
            <div><span>Previous</span></div>
        </a>
        {% endif %}

        <a class="img" href="{{ img }}"><img src="{{ img }}" alt="Page {{ pgnum }} of {{ book }}"></a>

        {% if has_next %}
        <a src="{{ next_img }}" class="nav prev" href="{{ next_link }}" title="Next page: {{ next_num }}">
            <img src="{{ next_img }}" alt="Page {{ next_num }} of {{ book }}">
            <div><span>Next<span></div>
        </a>
        {% endif %}
    </div>
</body>
''')

STYLE_CSS = '''
body {
    font-family: Montserrat, sans-serif;
}

h1 {
    font-size: 32pt;
}

h2 {
    margin-top: 2em;
    font-size: 24pt;
}

h4 {
    margin-top: 3em;
    font-size: 16pt;
}

ol.pglist {
    display: flex;
    flex-wrap: wrap;
    list-style: none;
}

ol.pglist li {
    margin: 10px;
    width: 100px;
    text-align: center;
}

ol.pglist li::before {
    content: attr(value);
    display: block;
    text-align: center;
    width: 100%;
    font-weight: bold;
}

body.page .img img {
    max-width: calc(100vw - 15em);
    max-height: calc(100vh - 8em);
}

body.page h1 {
    margin-bottom: .3em;
}

body.page .viewer {
    display: flex;
    align-items: center;
}

body.page .nav {
    margin: 10px;
    position: relative;
    vertical-align: top;
    flex-grow: 0;
}

body.page .nav div {
    position: absolute;
    top: 0;
    left: 0;
    width: 100%;
    height: 100%;
    z-index: 100;

    display: flex;
    justify-content: center;
    align-items: center;
}

body.page .links {
    margin-bottom: 10px;
}

a, a:hover, a:active, a:link {
    text-decoration: none;
    color: red;
    font-weight: bold;
}

a:visited {
    color: #800;
}
'''

imgname = lambda prefix, pgnum, orig_fn: f'ar-{prefix.lower()}-{pgnum:04}{path.splitext(orig_fn)[1].lower()}'
imgpath  = lambda prefix, pgnum, orig_fn: path.join('images', imgname(prefix, pgnum, orig_fn))
thumbpath = lambda prefix, pgnum: path.join('thumbs', imgname(prefix, pgnum, '_.png'))

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('-d', '--database', default='kochbuch.sqlite3', help='Metadata db path, default: kochbuch.sqlite3')
    parser.add_argument('-o', '--out', type=str, default=None, help='Output zip file. This or --title must be given.')
    parser.add_argument('img_path', default='.', nargs='?', help='Base directory of image files')
    parser.add_argument('-t', '--title', default='Image Archive', help='Product title (for headings and file names)')
    args = parser.parse_args()

    if not args.out and not args.title:
        raise ValueError('At least one of --out and --title must be given.')

    zip_name = args.out or f'{args.title}.zip'

    db = sqlite3.connect(args.database)
    db.execute('PRAGMA foreign_keys = ON')

    pics = {}
    print('Building file index')
    for pic in tqdm.tqdm(glob.glob(path.join(glob.escape(args.img_path), '**/*.jpg'), recursive=True)):
        with open(pic, 'rb') as f:
            hash = hashlib.sha3_256()
            hash.update(f.read())
            hash = hash.hexdigest()
        pics[hash] = pic

    with tempfile.TemporaryDirectory() as tmpdir:
        out = path.join(tmpdir, args.title)
        os.mkdir(out)

        print('Copying images')
        os.mkdir(path.join(out, 'images'))
        for hash, prefix, pgnum, orig_fn in db.execute('SELECT sha3, prefix, pgnum, filename FROM pics'):
            dst = path.join(out, imgpath(prefix, pgnum, orig_fn))
            shutil.copy(pics[hash], dst)
            pics[hash] = dst

        print('Generating Thumbnails')
        thumb_path = path.join(out, 'thumbs')
        os.mkdir(thumb_path)
        subprocess.check_call([
            'mogrify', '-format', 'png', '-path', thumb_path, '-thumbnail', '100x100',
            *pics.values() ])

        print('Writing indices')
        books = [ book for book, in db.execute('SELECT DISTINCT book FROM pics ORDER BY book') ]
        book_indices = []
        book_pages = []
        for book in books:
            book_dir = book
            os.mkdir(path.join(out, book_dir))

            entries = [ (value, f'pages/pg{pgnum}.html') for value, pgnum in db.execute(
                    '''SELECT value, pgnum FROM pic_tags
                    JOIN pics ON pics.id=pic_tags.pic
                    WHERE pic_tags.tag=(SELECT id FROM tags WHERE name="title")
                    AND book=?
                    AND value NOT NULL AND value != ""
                    ORDER BY value''', (book,)) ]
            idx_fn = path.join(book_dir, 'index.html')
            with open(path.join(out, idx_fn), 'w') as f:
                f.write(BOOK_IDX_TEMPL.render(title=args.title, book=book, entries=entries))

            results = db.execute('SELECT prefix, pgnum, filename, sha3 FROM pics WHERE book=? ORDER BY pgnum',
                    (book,)).fetchall()

            tmp = [ (pgnum, thumbpath(prefix, pgnum), f'{book}/pages/pg{pgnum}.html')
                    for prefix, pgnum, fn, hash in results ]
            book_pages.append((book, tmp))

            pages = [ (pgnum,
                       f'../{thumbpath(prefix, pgnum)}', f'../{imgpath(prefix, pgnum, fn)}', f'pages/pg{pgnum}.html',
                       hash)
                      for prefix, pgnum, fn, hash in results ]

            pages_fn = path.join(book_dir, 'pages.html')
            with open(path.join(out, pages_fn), 'w') as f:
                f.write(BOOK_PAGE_LIST_TEMPL.render(title=args.title, book=book, pages=pages))

            cats = [ (cat, path.join(book_dir, f'pages-{cat}.html')) for cat, in db.execute(
                    'SELECT DISTINCT category FROM pics WHERE book=? AND category NOT NULL', (book,)) ]
            for cat, cat_fn in cats:
                cat_pages = [ (pgnum, '../'+thumbpath(prefix, pgnum), f'pages/pg{pgnum}.html')
                        for prefix, pgnum, fn in
                        db.execute('SELECT prefix, pgnum, filename FROM pics WHERE book=? AND category=? ORDER BY pgnum',
                            (book, cat)) ]
                with open(path.join(out, cat_fn), 'w') as f:
                    f.write(CAT_PAGE_LIST_TEMPL.render(title=args.title, book=book, cat=cat, pages=cat_pages))

            book_indices.append((book, idx_fn, pages_fn, cats))

            page_dir = path.join(book_dir, 'pages')
            os.mkdir(path.join(out, page_dir))
            for prev, (pgnum, _thumb, img, link, hash), next in zip(
                    [None] + pages[:-1],
                    pages,
                    pages[1:] + [None]):

                titles = [ title for title, in db.execute('''
                        SELECT value FROM pic_tags JOIN pics ON pics.id=pic_tags.pic
                        WHERE sha3=? AND value NOT NULL AND value!=""
                ''', (hash,)) ]

                cat, = db.execute('SELECT category FROM pics WHERE sha3=?', (hash,)).fetchone()

                has_prev = prev is not None
                if prev:
                    has_prev = True
                    prev_num, prev_img, _1, prev_link, _2 = prev
                    prev_link = f'pg{prev_num}.html'
                else:
                    has_prev, prev_num, prev_img, prev_link = False, None, None, None

                has_next = next is not None
                if next:
                    has_next = True
                    next_num, next_img, _1, next_link, _2 = next
                    next_link = f'pg{next_num}.html'
                else:
                    has_next, next_num, next_img, next_link = False, None, None, None

                page_fn = path.join(page_dir, f'pg{pgnum}.html')
                with open(path.join(out, page_fn), 'w') as f:
                    f.write(PAGE_TEMPL.render(
                        title=args.title, page_list='../pages.html', index='../index.html', img=f'../{img}',
                        titles=titles, pgnum=pgnum, book=book, cat=cat, has_prev=has_prev, prev_link=prev_link,
                        prev_num=prev_num, prev_img=f'../{prev_img}', has_next=has_next, next_link=next_link,
                        next_num=next_num, next_img=f'../{next_img}'))

        entries = [ (value, f'{book}/pages/pg{pgnum}.html') for value, book, pgnum in db.execute(
                '''SELECT value, book, pgnum FROM pic_tags
                JOIN pics ON pics.id=pic_tags.pic
                WHERE pic_tags.tag=(SELECT id FROM tags WHERE name="title")
                AND value NOT NULL AND value != ""
                ORDER BY value''') ]

        with open(path.join(out, f'index.html'), 'w') as f:
            f.write(MAIN_IDX_TEMPL.render(title=args.title, entries=entries, books=book_indices, zip_name=zip_name))

        with open(path.join(out, f'pages.html'), 'w') as f:
            f.write(MAIN_PAGE_LIST_TEMPL.render(title=args.title, books=book_pages, parse=parse))

        with open(path.join(out, 'style.css'), 'w') as f:
            f.write(STYLE_CSS)

        print('Compressing output')
        subprocess.check_call(['zip', '-r', path.abspath(zip_name), args.title], cwd=tmpdir)
        print('Done.')

