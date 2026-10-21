
import html
import json
import os
import secrets
import sqlite3
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs
from urllib.request import urlopen
from urllib.error import URLError

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))
PUBLIC_URL = os.getenv("PUBLIC_URL", "").strip().rstrip("/")
DB = "linktrack.db"


def connect():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def init_db():
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            destination TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS clicks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            link_id INTEGER NOT NULL REFERENCES links(id),
            visited_at TEXT NOT NULL,
            consent TEXT NOT NULL DEFAULT 'not_asked'
        );

        CREATE TABLE IF NOT EXISTS locations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            click_id INTEGER UNIQUE NOT NULL REFERENCES clicks(id),
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            accuracy REAL,
            collected_at TEXT NOT NULL
        );
        """)


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def esc(value):
    return html.escape(str(value), quote=True)


def page(title, body):
    return f"""<!doctype html>
<html lang="id">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} — LinkTrack</title>
<style>
*{{box-sizing:border-box}}
body{{margin:0;background:#f3f6fb;color:#182238;
font:15px system-ui,-apple-system,sans-serif}}
header{{background:#101828;color:white;padding:22px max(20px,calc((100% - 1000px)/2))}}
header strong{{font-size:23px}}
main{{max-width:1000px;margin:28px auto;padding:0 18px}}
.card{{background:white;border:1px solid #e0e6ef;border-radius:15px;
padding:20px;margin-bottom:18px;overflow-wrap:anywhere}}
h1{{font-size:27px;margin:0 0 8px}}h2{{font-size:19px;margin-top:0}}
p,.muted{{color:#637087;line-height:1.65}}
input{{width:100%;padding:12px;border:1px solid #cbd5e1;
border-radius:9px;margin:6px 0 15px;font:inherit}}
button,.btn{{display:inline-block;border:0;border-radius:9px;
padding:11px 16px;background:#245ac5;color:white;font:inherit;
cursor:pointer;text-decoration:none}}
.secondary{{background:#e9eef8;color:#203451}}
label{{font-weight:600}}table{{width:100%;border-collapse:collapse}}
th,td{{text-align:left;padding:11px 8px;border-bottom:1px solid #e8edf4}}
th{{color:#637087;font-size:13px}}
a{{color:#245ac5}}.stats{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}}
.stat{{background:#f4f7fc;padding:15px;border-radius:10px}}
.num{{font-size:27px;font-weight:750}}
.small{{font-size:13px}}.notice{{background:#fff8e6;padding:12px;border-radius:9px}}
@media(max-width:650px){{.stats{{grid-template-columns:1fr}}
table{{font-size:13px}}th,td{{padding:8px 4px}}}}
</style>
</head>
<body>
<header><strong>📍 LinkTrack</strong><div class="small">Dashboard analitik link yang mengutamakan privasi</div></header>
<main>{body}</main>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def send_html(self, content, status=200):
        raw = content.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def send_json(self, data, status=200):
        raw = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def body(self):
        length = min(int(self.headers.get("Content-Length", "0")), 10000)
        return self.rfile.read(length).decode("utf-8", "replace")

    def do_GET(self):
        path = urlparse(self.path)
        if path.path == "/":
            return self.dashboard()

        if path.path.startswith("/r/"):
            token = path.path.removeprefix("/r/").strip("/")
            return self.visit(token)

        if path.path == "/health":
            return self.send_json({"status": "ok"})

        self.send_html(page("Tidak ditemukan",
            '<div class="card"><h1>404</h1><a href="/">Kembali ke dashboard</a></div>'), 404)

    def do_POST(self):
        path = urlparse(self.path).path

        if path == "/create":
            data = parse_qs(self.body())
            name = data.get("name", [""])[0].strip()[:100]
            destination = data.get("destination", [""])[0].strip()

            parsed = urlparse(destination)
            if (not name or len(destination) > 2000 or
                    parsed.scheme not in ("http", "https") or
                    not parsed.hostname or
                    any(c in destination for c in "\r\n")):
                return self.dashboard("Nama atau URL tidak valid.")

            token = secrets.token_urlsafe(9)
            with connect() as db:
                db.execute(
                    "INSERT INTO links(token,name,destination,created_at) VALUES(?,?,?,?)",
                    (token, name, destination, now())
                )
            self.redirect("/")
            return

        if path == "/api/location":
            try:
                data = json.loads(self.body())
                click_id = int(data["click_id"])
                lat = float(data["latitude"])
                lon = float(data["longitude"])
                accuracy = float(data.get("accuracy", 0))
                if (not -90 <= lat <= 90 or not -180 <= lon <= 180
                        or not 0 <= accuracy <= 100000):
                    raise ValueError("Koordinat tidak valid")

                with connect() as db:
                    click = db.execute(
                        "SELECT consent FROM clicks WHERE id=?", (click_id,)
                    ).fetchone()
                    if not click or click["consent"] != "granted":
                        return self.send_json({"error": "Izin tidak tercatat"}, 403)
                    db.execute("""
                        INSERT OR IGNORE INTO locations
                        (click_id,latitude,longitude,accuracy,collected_at)
                        VALUES(?,?,?,?,?)
                    """, (click_id, lat, lon, accuracy, now()))
                return self.send_json({"ok": True})
            except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                return self.send_json({"error": "Data tidak valid"}, 400)

        if path == "/api/consent":
            try:
                data = json.loads(self.body())
                click_id = int(data["click_id"])
                choice = data["choice"]
                if choice not in ("granted", "denied"):
                    raise ValueError()
                with connect() as db:
                    db.execute("UPDATE clicks SET consent=? WHERE id=?",
                               (choice, click_id))
                return self.send_json({"ok": True})
            except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                return self.send_json({"error": "Data tidak valid"}, 400)

        self.send_json({"error": "Tidak ditemukan"}, 404)

    def public_base_url(self):
        if PUBLIC_URL:
            return PUBLIC_URL
        host = self.headers.get("Host", "").strip()
        if not host or any(ch in host for ch in "\r\n/\\"):
            host = f"127.0.0.1:{PORT}"
        scheme = "http"
        if os.getenv("TRUST_PROXY_HEADERS", "").lower() in ("1", "true", "yes"):
            forwarded = self.headers.get("X-Forwarded-Proto", "").split(",")[0].strip().lower()
            if forwarded in ("http", "https"):
                scheme = forwarded
        return f"{scheme}://{host}"

    def redirect(self, path):
        self.send_response(303)
        self.send_header("Location", path)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def dashboard(self, error=""):
        with connect() as db:
            links = db.execute("""
                SELECT l.*,
                  (SELECT COUNT(*) FROM clicks c WHERE c.link_id=l.id) AS clicks,
                  (SELECT COUNT(*) FROM clicks c JOIN locations g ON g.click_id=c.id
                   WHERE c.link_id=l.id) AS locations
                FROM links l ORDER BY l.id DESC
            """).fetchall()
            total = db.execute("SELECT COUNT(*) FROM clicks").fetchone()[0]
            total_locations = db.execute("SELECT COUNT(*) FROM locations").fetchone()[0]
            records = db.execute("""
                SELECT c.id,c.visited_at,c.consent,l.name,
                       g.latitude,g.longitude,g.accuracy
                FROM clicks c JOIN links l ON l.id=c.link_id
                LEFT JOIN locations g ON g.click_id=c.id
                ORDER BY c.id DESC LIMIT 100
            """).fetchall()

        rows = ""
        for link in links:
            short = f"http://localhost:{PORT}/r/{esc(link['token'])}"
            rows += f"""<tr><td><b>{esc(link['name'])}</b><br>
            <span class="small">{esc(link['destination'])}</span></td>
            <td>{link['clicks']}</td><td>{link['locations']}</td>
            <td><a href="{short}">Buka</a> ·
            <button class="secondary" onclick="copyLink('{short}')">Salin</button></td></tr>"""

        if not rows:
            rows = '<tr><td colspan="4">Belum ada link. Buat link pertama di atas.</td></tr>'

        visit_rows = ""
        for record in records:
            location = "—"
            if record["latitude"] is not None:
                location = (f'<a target="_blank" rel="noreferrer" '
                    f'href="https://www.openstreetmap.org/?mlat={record["latitude"]}'
                    f'&mlon={record["longitude"]}#map=16/{record["latitude"]}/{record["longitude"]}">'
                    f'{record["latitude"]:.5f}, {record["longitude"]:.5f}</a>')
            visit_rows += f"""<tr><td>{esc(record['name'])}</td>
            <td>{esc(record['visited_at'])}</td><td>{esc(record['consent'])}</td>
            <td>{location}</td></tr>"""
        if not visit_rows:
            visit_rows = '<tr><td colspan="4">Belum ada kunjungan.</td></tr>'

        body = f"""
        <div class="card"><h1>Dashboard</h1>
        <p>Kelola link dan lihat kunjungan yang tercatat di komputer ini.</p>
        <div class="stats">
          <div class="stat"><div class="muted">Total link</div><div class="num">{len(links)}</div></div>
          <div class="stat"><div class="muted">Total klik</div><div class="num">{total}</div></div>
          <div class="stat"><div class="muted">Lokasi berizin</div><div class="num">{total_locations}</div></div>
        </div></div>
        <div class="card"><h2>＋ Buat tracking link</h2>
        <form method="POST" action="/create">
        <label>Nama campaign</label><input name="name" required maxlength="100" value="My campaign">
        <label>URL tujuan</label><input name="destination" type="url" required value="https://example.com">
        <button type="submit">Buat link</button></form>
        {f'<p style="color:#b42318">{esc(error)}</p>' if error else ''}
        </div>
        <div class="card"><h2>Link saya</h2><table><tr><th>Campaign</th><th>Klik</th><th>GPS</th><th>Link lokal</th></tr>{rows}</table>
        <p class="small">Link localhost hanya dapat dibuka di komputer ini. Link ini bukan link publik.</p></div>
        <div class="card"><h2>Aktivitas pengunjung</h2>
        <p>Koordinat hanya ditampilkan jika pengunjung menyetujui permintaan lokasi.</p>
        <table><tr><th>Campaign</th><th>Waktu (UTC)</th><th>Izin GPS</th><th>Lokasi</th></tr>{visit_rows}</table>
        </div>
        <div class="card"><h2>Privasi</h2>
        <p>Halaman kunjungan menjelaskan permintaan lokasi sebelum pengunjung memilih.
        Menolak GPS tidak menghalangi pengunjung menuju URL tujuan. Lokasi IP tidak
        diambil oleh aplikasi ini. Data disimpan di file lokal <code>linktrack.db</code>.</p>
        <p class="small">Ini adalah aplikasi lokal tanpa autentikasi admin. Jangan
        mengeksposnya ke internet sebelum menambahkan autentikasi, HTTPS, perlindungan
        CSRF, rate limiting, kebijakan retensi dan mekanisme penghapusan data.</p></div>
        <script>
        async function copyLink(url) {{
          try {{ await navigator.clipboard.writeText(url); alert('Link disalin: '+url); }}
          catch(e) {{ prompt('Salin link ini:',url); }}
        }}
        </script>
        """
        self.send_html(page("Dashboard", body))

    def visit(self, token):
        with connect() as db:
            link = db.execute("SELECT * FROM links WHERE token=?", (token,)).fetchone()
            if not link:
                return self.send_html(page("Link tidak ditemukan",
                    '<div class="card"><h1>Link tidak ditemukan</h1></div>'), 404)
            cursor = db.execute(
                "INSERT INTO clicks(link_id,visited_at) VALUES(?,?)",
                (link["id"], now())
            )
            click_id = cursor.lastrowid

        destination_json = json.dumps(link["destination"])
        body = f"""
        <div class="card">
          <h1>Melanjutkan ke situs tujuan</h1>
          <p>Anda akan membuka: <b>{esc(link['destination'])}</b></p>
          <div class="notice">
            <b>Berbagi lokasi (opsional)</b>
            <p>Jika Anda memilih tombol di bawah, browser akan meminta izin lokasi.
            Koordinat dan perkiraan akurasi akan dibagikan kepada pengelola tautan.
            Anda juga dapat langsung melanjutkan tanpa membagikan lokasi.</p>
          </div>
          <button id="shareButton" type="button" onclick="shareLocation()">Izinkan dan bagikan lokasi</button>
          <p id="status" role="status" aria-live="polite"></p>
          <p><a class="btn" href="{esc(link['destination'])}" rel="noreferrer">Lanjut ke situs tujuan</a></p>
          <p class="small">Halaman ini mencatat kunjungan tautan. Lokasi tidak dikumpulkan tanpa tindakan dan izin Anda.</p>
        </div>
        <script>
        const destination = {destination_json};
        const clickId = {click_id};
        const statusEl = document.getElementById('status');
        const shareButton = document.getElementById('shareButton');

        async function saveConsent(choice) {{
          const response = await fetch('/api/consent', {{
            method: 'POST',
            headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify({{click_id: clickId, choice}})
          }});
          if (!response.ok) throw new Error('Tidak dapat menyimpan pilihan.');
        }}

        async function shareLocation() {{
          if (!navigator.geolocation) {{
            statusEl.textContent = 'Browser tidak mendukung lokasi. Anda dapat melanjutkan ke situs tujuan.';
            return;
          }}
          shareButton.disabled = true;
          statusEl.textContent = 'Silakan pilih izinkan pada permintaan lokasi browser jika Anda ingin berbagi.';
          navigator.geolocation.getCurrentPosition(async (pos) => {{
            try {{
              await saveConsent('granted');
              const response = await fetch('/api/location', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{
                  click_id: clickId,
                  latitude: pos.coords.latitude,
                  longitude: pos.coords.longitude,
                  accuracy: pos.coords.accuracy
                }})
              }});
              if (!response.ok) throw new Error('Lokasi tidak dapat disimpan.');
              statusEl.textContent = 'Lokasi berhasil dibagikan. Mengalihkan ke situs tujuan…';
              setTimeout(() => location.replace(destination), 500);
            }} catch (error) {{
              statusEl.textContent = 'Lokasi tidak tersimpan. Anda tetap dapat melanjutkan menggunakan tautan di atas.';
              shareButton.disabled = false;
            }}
          }}, async () => {{
            try {{ await saveConsent('denied'); }} catch (_) {{}}
            statusEl.textContent = 'Lokasi tidak dibagikan. Anda tetap dapat melanjutkan menggunakan tautan di atas.';
            shareButton.disabled = false;
          }}, {{enableHighAccuracy: true, timeout: 10000, maximumAge: 0}});
        }}
        </script>
        """
        self.send_html(page("Melanjutkan ke situs tujuan", body))

    def log_message(self, format, *args):
        # Keep terminal output simple; do not print request details.
        pass


if __name__ == "__main__":
    init_db()
    print(f"LinkTrack berjalan pada {HOST}:{PORT}")
    if PUBLIC_URL:
        print(f"URL publik: {PUBLIC_URL}")
    print("Tekan Ctrl+C untuk menghentikan server.")
    try:
        HTTPServer((HOST, PORT), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\nLinkTrack dihentikan.")
