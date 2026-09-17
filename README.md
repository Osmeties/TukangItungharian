# Bot Laporan Join & Left Grup Telegram

Bot ini menghitung jumlah member yang join dan left di grup Telegram
per hari, melacak **total member saat ini**, lalu otomatis mengirim
laporan setiap pergantian hari (00:00 WIB) via **DM ke semua admin
grup**.

Data disimpan di **PostgreSQL yang di-hosting di Railway**.

## Setup di Railway

1. **Buat project baru di Railway**, lalu deploy repo/folder ini
   (via GitHub atau `railway up`).
2. **Tambahkan database**: di dalam project yang sama, klik
   "+ New" → "Database" → "Add PostgreSQL". Railway akan otomatis
   membuat service Postgres terpisah.
3. **Hubungkan `DATABASE_URL` ke service bot**: buka service bot →
   tab **Variables** → klik "+ New Variable" → pilih "Add Reference"
   → arahkan ke variabel `DATABASE_URL` milik service Postgres yang
   baru dibuat. (Kalau Railway sudah otomatis menyediakan referensi
   ini saat kamu menambahkan Postgres di project yang sama, kamu
   tinggal pastikan saja variabelnya sudah muncul di service bot.)
4. **Set `BOT_TOKEN`**: di tab Variables service bot yang sama,
   tambahkan `BOT_TOKEN` dengan token dari
   [@BotFather](https://t.me/BotFather).
5. **Pastikan tipe service = Worker, bukan Web**: bot ini pakai
   `run_polling()`, jadi tidak butuh port/HTTP. File `Procfile` di
   repo ini sudah mendefinisikan `worker: python bot.py` — Railway
   akan otomatis mendeteksi dan menjalankannya sebagai worker
   process (bukan web service), jadi kamu tidak perlu expose port.
6. Deploy. Cek tab **Logs** di service bot — kalau muncul
   `Database siap (PostgreSQL).` lalu `Bot berjalan...`, berarti
   koneksi ke Postgres sudah berhasil.
7. Tambahkan bot ke grup yang ingin dipantau, lalu **jadikan bot
   sebagai admin grup** dengan izin **"Delete messages"** minimal
   dicentang. Ini wajib — tanpa jadi admin, bot tidak akan menerima
   event join/left sama sekali (jadi statistik akan selalu 0), dan
   tanpa izin "Delete messages" fitur hapus pesan join tidak akan bisa
   jalan (akan gagal diam-diam dan cuma tercatat di log).
8. **Wajib**: setiap admin grup harus chat pribadi ke bot dan ketik
   `/start` minimal sekali. Ini syarat dari Telegram — bot tidak bisa
   memulai DM ke user yang belum pernah membuka chat dengannya.

## Jalankan secara lokal (opsional, untuk testing)

Kalau mau coba dulu di komputer sendiri sebelum deploy ke Railway,
kamu tetap butuh Postgres yang bisa diakses (bisa Postgres lokal,
atau langsung pakai `DATABASE_URL` dari instance Railway kamu):

```bash
pip install -r requirements.txt
export BOT_TOKEN="isi_token_dari_botfather"
export DATABASE_URL="postgresql://user:password@host:port/dbname"
python bot.py
```

## Fitur

- **Hitung otomatis**: setiap ada yang join/left grup, bot mencatatnya.
- **Total member**: setiap kali laporan dibuat (baik `/report` maupun
  laporan otomatis 00:00), bot mengambil jumlah total member grup
  langsung dari Telegram dan menampilkannya di laporan.
- **Laporan harian otomatis**: tiap jam 00:00, bot ambil daftar admin
  grup secara real-time (jadi kalau admin bertambah/berkurang, otomatis
  ikut ter-update tanpa perlu ubah kode) dan kirim laporan hari
  sebelumnya (join, left, total member) ke DM masing-masing admin.
- **Laporan on-demand**: admin bisa ketik `/report` di dalam grup atau
  di DM untuk minta laporan hari berjalan.
- **Multi-grup & multi-admin**: bot bisa dipasang di banyak grup
  sekaligus, dan tiap grup boleh punya admin lebih dari satu — semua
  admin akan menerima laporan grup masing-masing.
- **Hapus pesan join**: setiap ada pesan sistem "X added Y" ATAU "X
  joined the group", bot langsung menghapus pesan itu supaya grup
  tidak penuh notifikasi join. Membernya sendiri **tidak**
  dikeluarkan/di-kick — cuma pesannya yang dihapus. Pengecualian:
  pesan soal bot ini sendiri yang ditambahkan ke grup tidak dihapus.
  Deteksi join/left untuk statistik tetap jalan seperti biasa lewat
  event status member (`chat_member`) dari Telegram, terpisah dari
  penghapusan pesan ini — jadi statistik tidak terpengaruh sama
  sekali, tetap akurat meskipun grup mengaktifkan setelan "Hide
  Members Who Joined/Left".

## Penyimpanan data (PostgreSQL di Railway)

Data disimpan di database **PostgreSQL**, bukan file lokal seperti
versi sebelumnya (JSON / SQLite). Alasannya:

- Filesystem service di Railway itu **ephemeral** — kalau proses
  bot di-restart/redeploy, file lokal seperti `bot_data.db` bisa
  hilang. Postgres di Railway hidup sebagai service terpisah yang
  persisten, jadi data aman meskipun bot-nya restart.
- Setiap perubahan (join/left/total member) ditulis lewat transaksi
  database yang **atomik** — kalau proses bot mati mendadak di tengah
  jalan, data yang sudah tersimpan sebelumnya tidak ikut rusak/hilang.
- Histori total member per hari juga tersimpan permanen, tidak cuma
  dihitung ulang tiap kali dibutuhkan.
- Koneksi ke database dilakukan lewat connection pool (`asyncpg`)
  yang dibuat sekali saat bot startup, jadi tidak buka-tutup koneksi
  di setiap query.

Ada 2 tabel:
- `chats` — daftar grup yang pernah berinteraksi dengan bot.
- `daily_stats` — jumlah join, left, dan total member per grup per
  tanggal.

### Backup

Railway PostgreSQL bisa di-backup lewat `pg_dump` menggunakan
`DATABASE_URL` yang sama, misalnya:
```bash
pg_dump "$DATABASE_URL" > "backup_$(date +%Y%m%d).sql"
```
Railway juga punya fitur backup/snapshot bawaan untuk plugin
database di beberapa paket — cek tab database di dashboard Railway.

## Batasan penting

- Laporan **tidak bisa** dikirim di dalam grup dengan status "hanya
  admin yang bisa lihat" — Telegram tidak punya fitur seperti itu untuk
  pesan grup biasa. Solusinya laporan dikirim via **DM** ke tiap admin,
  itulah kenapa langkah `/start` di atas wajib dilakukan.
- Bot perlu tetap berjalan (running) 24 jam supaya bisa mendeteksi
  event join/left dan mengirim laporan tepat waktu — ini sudah sesuai
  dengan cara kerja service di Railway (selama service tidak di-sleep
  manual).

## Ubah zona waktu

Default zona waktu adalah `Asia/Jakarta` (WIB). Kalau grup kamu pakai
zona waktu lain, ubah baris ini di `bot.py`:
```python
TIMEZONE = ZoneInfo("Asia/Jakarta")
```
