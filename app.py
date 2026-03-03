from flask import Flask, render_template, request, redirect, url_for, g
import sqlite3
import hashlib
import datetime
import os

app = Flask(__name__)
DATABASE = 'bbs.db'

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def init_db():
    with app.app_context():
        db = get_db()
        db.executescript('''
            CREATE TABLE IF NOT EXISTS boards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                description TEXT
            );
            CREATE TABLE IF NOT EXISTS threads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                board_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                last_post_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                post_count INTEGER DEFAULT 0,
                FOREIGN KEY (board_id) REFERENCES boards(id)
            );
            CREATE TABLE IF NOT EXISTS posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id INTEGER NOT NULL,
                post_number INTEGER NOT NULL,
                name TEXT NOT NULL DEFAULT '名無しさん',
                email TEXT,
                message TEXT NOT NULL,
                ip_hash TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (thread_id) REFERENCES threads(id)
            );
            INSERT OR IGNORE INTO boards (slug, name, description) VALUES ('news', 'ニュース板', 'ニュース全般');
            INSERT OR IGNORE INTO boards (slug, name, description) VALUES ('nanka', 'なんか', 'なんかいろいろ');
        ''')
        db.commit()

def generate_id(ip, date_str):
    raw = ip + date_str + 'shinya_salt_2026'
    return hashlib.md5(raw.encode()).hexdigest()[:8].upper()

def get_ip_hash(ip):
    return hashlib.sha256(ip.encode()).hexdigest()[:8].upper()

def format_datetime(dt_str):
    """Format datetime for display like 2channel style"""
    try:
        if isinstance(dt_str, str):
            dt = datetime.datetime.strptime(dt_str, '%Y-%m-%d %H:%M:%S')
        else:
            dt = dt_str
        weekdays = ['月', '火', '水', '木', '金', '土', '日']
        wd = weekdays[dt.weekday()]
        return dt.strftime(f'%Y/%m/%d({wd}) %H:%M:%S')
    except:
        return str(dt_str)

def get_current_time():
    return datetime.datetime.now().strftime('%H:%M')

import re
from markupsafe import Markup, escape

def replace_anchors(text):
    """Convert >>N anchor refs to links and URLs to hyperlinks"""
    # Escape HTML first
    text = str(escape(text))
    # Convert >>N to anchor links
    text = re.sub(r'&gt;&gt;(\d+)', r'<a href="#\1">&gt;&gt;\1</a>', text)
    # Convert URLs to links
    text = re.sub(r'(https?://[^\s<]+)', r'<a href="\1">\1</a>', text)
    return Markup(text)

app.jinja_env.globals['get_current_time'] = get_current_time
app.jinja_env.filters['fmt_dt'] = format_datetime
app.jinja_env.filters['replace_anchors'] = replace_anchors

# ---- Routes ----

@app.route('/')
def top():
    db = get_db()
    # Get recent posts across all boards (last 1 hour)
    one_hour_ago = (datetime.datetime.now() - datetime.timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')
    recent_posts = db.execute('''
        SELECT p.*, t.title as thread_title, t.id as thread_id, b.slug as board_slug, b.name as board_name
        FROM posts p
        JOIN threads t ON p.thread_id = t.id
        JOIN boards b ON t.board_id = b.id
        WHERE p.created_at >= ?
        ORDER BY p.created_at ASC
    ''', (one_hour_ago,)).fetchall()

    total_posts = db.execute('SELECT COUNT(*) as c FROM posts').fetchone()['c']
    now_time = get_current_time()
    return render_template('top.html', recent_posts=recent_posts, total_posts=total_posts, now_time=now_time)

@app.route('/list')
def board_list():
    db = get_db()
    boards = db.execute('''
        SELECT b.*, COUNT(t.id) as thread_count
        FROM boards b
        LEFT JOIN threads t ON b.id = t.board_id
        GROUP BY b.id
    ''').fetchall()
    return render_template('list.html', boards=boards)

@app.route('/board/<slug>')
def board(slug):
    db = get_db()
    board = db.execute('SELECT * FROM boards WHERE slug = ?', (slug,)).fetchone()
    if not board:
        return '板が見つかりません', 404

    search_query = request.args.get('q', '')
    if search_query:
        threads = db.execute('''
            SELECT t.* FROM threads t
            WHERE t.board_id = ? AND t.title LIKE ?
            ORDER BY t.last_post_at DESC
        ''', (board['id'], f'%{search_query}%')).fetchall()
    else:
        threads = db.execute('''
            SELECT t.* FROM threads t
            WHERE t.board_id = ?
            ORDER BY t.last_post_at DESC
        ''', (board['id'],)).fetchall()

    # Get first post (OP) for each thread to get the author name
    thread_ops = {}
    for t in threads:
        op = db.execute('SELECT name FROM posts WHERE thread_id = ? ORDER BY post_number ASC LIMIT 1', (t['id'],)).fetchone()
        if op:
            thread_ops[t['id']] = op['name']

    now_time = get_current_time()
    return render_template('ita.html', board=board, threads=threads, thread_ops=thread_ops, now_time=now_time, search_query=search_query)

@app.route('/board/<slug>/thread/<int:thread_id>')
def thread(slug, thread_id):
    db = get_db()
    board = db.execute('SELECT * FROM boards WHERE slug = ?', (slug,)).fetchone()
    if not board:
        return '板が見つかりません', 404

    thread = db.execute('SELECT * FROM threads WHERE id = ? AND board_id = ?', (thread_id, board['id'])).fetchone()
    if not thread:
        return 'スレッドが見つかりません', 404

    search_query = request.args.get('q', '')
    if search_query:
        posts = db.execute('''
            SELECT * FROM posts WHERE thread_id = ? AND message LIKE ?
            ORDER BY post_number ASC
        ''', (thread_id, f'%{search_query}%')).fetchall()
    else:
        posts = db.execute('''
            SELECT * FROM posts WHERE thread_id = ?
            ORDER BY post_number ASC
        ''', (thread_id,)).fetchall()

    return render_template('sure.html', board=board, thread=thread, posts=posts, search_query=search_query)

@app.route('/board/<slug>/post_thread', methods=['POST'])
def post_thread(slug):
    db = get_db()
    board = db.execute('SELECT * FROM boards WHERE slug = ?', (slug,)).fetchone()
    if not board:
        return '板が見つかりません', 404

    name = request.form.get('name', '').strip() or '名無しさん'
    thread_title = request.form.get('threadn', '').strip()
    message = request.form.get('message', '').strip()

    if not thread_title or not message:
        return redirect(url_for('board', slug=slug))

    ip = request.remote_addr or '0.0.0.0'
    ip_hash = get_ip_hash(ip)
    now = datetime.datetime.now()
    date_str = now.strftime('%Y%m%d')
    user_id = generate_id(ip, date_str)

    # Create thread
    cur = db.execute('''
        INSERT INTO threads (board_id, title, created_at, last_post_at, post_count)
        VALUES (?, ?, ?, ?, 1)
    ''', (board['id'], thread_title, now.strftime('%Y-%m-%d %H:%M:%S'), now.strftime('%Y-%m-%d %H:%M:%S')))
    thread_id = cur.lastrowid

    # Insert first post
    db.execute('''
        INSERT INTO posts (thread_id, post_number, name, message, ip_hash, created_at)
        VALUES (?, 1, ?, ?, ?, ?)
    ''', (thread_id, name, message, user_id, now.strftime('%Y-%m-%d %H:%M:%S')))
    db.commit()

    return redirect(url_for('thread', slug=slug, thread_id=thread_id))

@app.route('/board/<slug>/thread/<int:thread_id>/post', methods=['POST'])
def post_reply(slug, thread_id):
    db = get_db()
    board = db.execute('SELECT * FROM boards WHERE slug = ?', (slug,)).fetchone()
    if not board:
        return '板が見つかりません', 404

    thread = db.execute('SELECT * FROM threads WHERE id = ? AND board_id = ?', (thread_id, board['id'])).fetchone()
    if not thread:
        return 'スレッドが見つかりません', 404

    name = request.form.get('name', '').strip() or '名無しさん'
    email = request.form.get('email', '').strip()
    message = request.form.get('message', '').strip()

    if not message:
        return redirect(url_for('thread', slug=slug, thread_id=thread_id))

    ip = request.remote_addr or '0.0.0.0'
    ip_hash = get_ip_hash(ip)
    now = datetime.datetime.now()
    date_str = now.strftime('%Y%m%d')
    user_id = generate_id(ip, date_str)

    # Get next post number
    last = db.execute('SELECT MAX(post_number) as m FROM posts WHERE thread_id = ?', (thread_id,)).fetchone()
    post_number = (last['m'] or 0) + 1

    db.execute('''
        INSERT INTO posts (thread_id, post_number, name, email, message, ip_hash, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (thread_id, post_number, name, email, message, user_id, now.strftime('%Y-%m-%d %H:%M:%S')))

    db.execute('''
        UPDATE threads SET last_post_at = ?, post_count = post_count + 1 WHERE id = ?
    ''', (now.strftime('%Y-%m-%d %H:%M:%S'), thread_id))
    db.commit()

    # sage support: if email is 'sage', don't bump
    return redirect(url_for('thread', slug=slug, thread_id=thread_id) + f'#{post_number}')

if __name__ == '__main__':
    init_db()
    app.run(host="0.0.0.0",debug=True, port=5000)
