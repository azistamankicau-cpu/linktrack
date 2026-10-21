# LinkTrack (updated)

## Menjalankan lokal
1. Pastikan Python 3.10+ tersedia.
2. Jalankan `python app.py`.
3. Buka `http://localhost:8000`.

## URL tracking dinamis
- Secara default, URL dibuat dari header `Host` pada request dashboard.
- Untuk domain deployment, set environment variable `PUBLIC_URL`, misalnya `https://track.example.com`.
- Jalankan aplikasi di balik HTTPS reverse proxy agar browser mengizinkan Geolocation.
- Jika proxy meneruskan `X-Forwarded-Proto`, set `TRUST_PROXY_HEADERS=1` hanya ketika request memang datang dari proxy tepercaya.
- `HOST` dan `PORT` juga dapat diatur melalui environment variable.

Contoh PowerShell:
```powershell
$env:PUBLIC_URL="https://track.example.com"
$env:HOST="0.0.0.0"
$env:PORT="8000"
python app.py
```

## Privasi lokasi
Lokasi bersifat opsional dan hanya dikirim setelah pengunjung menekan tombol berbagi lokasi serta memberikan izin browser. Jika ditolak atau gagal, pengunjung tetap diarahkan ke URL tujuan. Opsi geolocation memakai `enableHighAccuracy: true`, `timeout: 10000`, `maximumAge: 0`; nilai `coords.accuracy` disimpan dalam meter pada tabel `locations.accuracy`.

## Penting sebelum deployment publik
Aplikasi ini adalah server HTTP sederhana. Sebelum membuka ke internet, tambahkan autentikasi dashboard, HTTPS, perlindungan CSRF, rate limiting, pembatasan akses database, serta kebijakan retensi dan penghapusan data. `PUBLIC_URL` menentukan URL yang ditampilkan; variabel ini tidak membuat server otomatis dapat diakses dari internet. Anda tetap memerlukan hosting/domain dan konfigurasi jaringan yang sesuai.
