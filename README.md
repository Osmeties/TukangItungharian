# Bot Laporan Join & Left Grup Telegram

Bot ini menghitung jumlah member yang join dan left di grup Telegram
per hari, lalu otomatis mengirim laporan setiap pergantian hari
(00:00 WIB) via **DM ke semua admin grup**.

## Cara Setup

1. Buat bot baru via [@BotFather](https://t.me/BotFather) di Telegram,
   catat token yang diberikan.
2. Install dependency:
   ```bash
   pip install -r requirements.txt
   ```
3. Set token bot sebagai environment variable (atau edit langsung
   variabel `BOT_TOKEN` di `bot.py`):
   ```bash
   export BOT_TOKEN="isi_token_dari_botfather"
   ```
4. Jalankan bot:
   ```bash
   python bot.py
   ```
5. Tambahkan bot ke grup yang ingin dipantau.
6. **Wajib**: setiap admin grup harus chat pribadi ke bot dan ketik
   `/start` minimal sekali. Ini syarat dari Telegram — bot tidak bisa
   memulai DM ke user yang belum pernah membuka chat dengannya.

## Fitur

- **Hitung otomatis**: setiap ada yang join/left grup, bot mencatatnya.
- **Laporan harian otomatis**: tiap jam 00:00, bot ambil daftar admin
  grup secara real-time (jadi kalau admin bertambah/berkurang, otomatis
  ikut ter-update tanpa perlu ubah kode) dan kirim laporan hari
  sebelumnya ke DM masing-masing admin.
- **Laporan on-demand**: admin bisa ketik `/report` di dalam grup untuk
  minta laporan hari berjalan, hasilnya dikirim ke DM admin tersebut.
- **Multi-grup & multi-admin**: bot bisa dipasang di banyak grup
  sekaligus, dan tiap grup boleh punya admin lebih dari satu — semua
  admin akan menerima laporan grup masing-masing.

## Batasan penting

- Laporan **tidak bisa** dikirim di dalam grup dengan status "hanya
  admin yang bisa lihat" — Telegram tidak punya fitur seperti itu untuk
  pesan grup biasa. Solusinya laporan dikirim via **DM** ke tiap admin,
  itulah kenapa langkah `/start` di atas wajib dilakukan.
- Data disimpan di file lokal `member_stats.json`. Untuk penggunaan
  jangka panjang/skala besar, sebaiknya diganti ke database (SQLite/
  PostgreSQL) — bisa saya bantu upgrade kalau dibutuhkan.
- Bot perlu tetap berjalan (running) 24 jam supaya bisa mendeteksi
  event join/left dan mengirim laporan tepat waktu — bisa dijalankan di
  VPS, Railway, atau layanan hosting lain dengan proses yang jalan terus.

## Ubah zona waktu

Default zona waktu adalah `Asia/Jakarta` (WIB). Kalau grup kamu pakai
zona waktu lain, ubah baris ini di `bot.py`:
```python
TIMEZONE = ZoneInfo("Asia/Jakarta")
```
