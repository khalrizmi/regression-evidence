# Regression Evidence

Tool lokal untuk regresi manual Android. Screenshot dari HP otomatis masuk ke TC yang sedang aktif,
preview dokumen realtime, generate `.docx` bukti, dan copy hasil Result/Remark ke Google Sheet.

Cara kerja lengkap: [docs/how-it-works.md](docs/how-it-works.md).

## Mulai cepat

1. Install **Python 3.10+** ([Mac](#setup-di-mac) · [Windows](#setup-di-windows)).
2. Double-click **`run.command`** (Mac) atau **`run.bat`** (Windows).
3. Browser terbuka ke <http://127.0.0.1:8765>.
4. Colok HP dan pilih mode USB **"File transfer"** ([detail](#menghubungkan-hp-android)).

Pertama kali, launcher membuat `.venv` dan meng-install dependency (±1–2 menit, butuh internet).
Berikutnya langsung jalan.

## Daftar isi

- [Kebutuhan](#kebutuhan)
- [Setup di Mac](#setup-di-mac)
- [Setup di Windows](#setup-di-windows)
- [Menghubungkan HP Android](#menghubungkan-hp-android)
- [Menjalankan dan menghentikan](#menjalankan-dan-menghentikan)
- [Pemakaian singkat](#pemakaian-singkat)
- [Konfigurasi](#konfigurasi-configjson)
- [Troubleshooting](#troubleshooting)
- [Keamanan data](#keamanan-data)
- [Development](#development)

---

## Kebutuhan

| Kebutuhan | Keterangan |
| --- | --- |
| Laptop | macOS, atau Windows 10/11 |
| Python | 3.10 atau lebih baru |
| HP Android | Satu HP per laptop; boleh diganti di tengah session |
| Kabel USB | Kabel **data**, bukan kabel khusus charging |
| File TC | `.xlsx` dengan kolom `No TC`, `Function`, `Scenario` |
| Opsional | Android platform-tools (adb), hanya untuk HP yang boleh USB debugging |

---

## Setup di Mac

1. **Install Python** lewat Terminal:

   ```sh
   brew install python
   ```

   Belum punya Homebrew? Ikuti <https://brew.sh>, atau install dari <https://www.python.org/downloads/>.

2. **Cek Python:**

   ```sh
   python3 --version   # harus 3.10 atau lebih baru
   ```

3. **Jalankan pertama kali:** di Finder, **klik kanan `run.command` → Open → Open**.
   macOS menolak double-click pada file dari internet sampai dibuka sekali lewat klik kanan.

4. Tunggu sampai muncul `Berjalan di http://127.0.0.1:8765/`. Browser terbuka sendiri.

Berikutnya cukup double-click `run.command`.

---

## Setup di Windows

1. **Install Python** dari <https://www.python.org/downloads/windows/>.
   Di layar pertama installer, **centang "Add python.exe to PATH"**, lalu klik *Install Now*.

   > Jangan pakai shortcut "python" dari Microsoft Store. Kalau `python` di Command Prompt membuka
   > Microsoft Store, Python asli belum terpasang.

   Alternatif lewat terminal (tanpa admin):

   ```bat
   winget install -e --id Python.Python.3.12 --scope user
   ```

2. **Tutup lalu buka lagi** Command Prompt / Explorer supaya PATH baru terbaca, lalu cek:

   ```bat
   py --version
   ```

3. **Double-click `run.bat`.** Jendela hitam terbuka. Pertama kali dibuat `.venv` dan dependency
   di-install (termasuk `pywin32` untuk USB File transfer).

4. Browser terbuka ke <http://127.0.0.1:8765>. **Jangan tutup jendela hitam** selama memakai tool.

---

## Menghubungkan HP Android

Capture **selalu otomatis**. Tool memilih sendiri jalurnya:

- **adb** bila ada HP dengan USB debugging aktif;
- **USB File transfer** untuk HP lainnya.

HP boleh diganti kapan saja, juga di tengah session.

| | USB File transfer | adb (USB debugging) |
| --- | --- | --- |
| Perlu Developer options | Tidak | Ya |
| Install tambahan | Tidak (Windows: `pywin32` otomatis) | Android platform-tools; Windows: USB driver vendor HP |
| Screenshot masuk | ±2–3 dtk (Windows), ±1 dtk (Mac) | ±1–2 dtk |
| Jeda setelah kabel dicolok | Windows ±20–70 dtk, Mac ±40–70 dtk | Tidak ada |
| Versi build terbaca otomatis | Tidak (isi manual) | Ya, bila `package` diisi di config |

### USB File transfer (HP tanpa Developer options)

1. Colok HP dengan kabel USB.
2. **Buka kunci HP.**
3. Tarik notifikasi USB di HP, lalu pilih **"File transfer"** (kadang tertulis "Transfer file" atau "MTP").
4. Tunggu status di tool menjadi terhubung. Screenshot yang diambil selama jeda awal tetap masuk, hanya tertunda.

Penting:

- **Setiap kabel dicabut-colok, HP kembali ke mode "Charging".** Pilih "File transfer" lagi.
- Jaga layar HP tidak terkunci. Di sebagian HP, file disembunyikan saat terkunci.
- Biarkan kabel tetap tercolok selama test.

### adb (HP dengan USB debugging)

1. **Install Android platform-tools:** <https://developer.android.com/tools/releases/platform-tools>.
   Ekstrak, lalu tambahkan foldernya ke PATH. Tool juga otomatis mencari di lokasi Android SDK:
   - Mac: `~/Library/Android/sdk/platform-tools`
   - Windows: `%LOCALAPPDATA%\Android\Sdk\platform-tools`
   - `ANDROID_HOME` / `ANDROID_SDK_ROOT`
2. **Windows:** install USB driver dari vendor HP (Samsung, Xiaomi, dll.).
3. Di HP: **Settings → About phone → ketuk "Build number" 7×** untuk membuka Developer options.
4. **Developer options → USB debugging → ON.**
5. Colok HP, lalu izinkan dialog **"Allow USB debugging"** di HP.
6. Cek: `adb devices` menampilkan HP dengan status `device`.

> Jangan aktifkan Developer options di HP yang tidak boleh. Pakai USB File transfer.

---

## Menjalankan dan menghentikan

| Aksi | Mac | Windows |
| --- | --- | --- |
| Menjalankan | Double-click `run.command` | Double-click `run.bat` |
| Membuka lagi browser | Jalankan launcher lagi: membuka tab ke server yang sudah jalan | sama |
| Berhenti | Tutup jendela Terminal, atau `Ctrl+C` | Tutup jendela hitam, atau `Ctrl+C` |

- Semua data tersimpan otomatis. Saat dibuka lagi, pilih session lalu **Lanjutkan**.
- Screenshot yang diambil saat tool tertutup **tidak** ditangkap.
- Launcher meng-install ulang dependency hanya bila `requirements.txt` berubah.

---

## Pemakaian singkat

1. **New session:** pilih file `.xlsx` → sheet → kolom Result → versi build → **Mulai**.
   Pilih "(tanpa kolom result)" bila semua TC ingin mulai dari Todo.
2. Pilih TC (klik, atau `↑` `↓`), jalankan test, lalu tekan tombol screenshot di HP. Gambar masuk ke TC aktif.
3. Tandai `P` Pass / `F` Fail / `S` Skip, isi Remark bila perlu.
4. **Preview Doc** untuk melihat susunan dokumen kapan saja.
5. **Generate DOCX** → file di `sessions/<session>/out/`. Upload ke Drive / copy ke tab Google Doc.
6. **Copy Result / Copy Remark** → klik sel baris pertama di kolom yang sesuai di Google Sheet → `Ctrl/Cmd+V`.
7. Dokumen sudah di-upload: **Hapus** session di layar awal (permanen, termasuk screenshot).

Detail alur, hotkey, Sync Excel, dan Export/Import ada di [docs/how-it-works.md](docs/how-it-works.md).

---

## Konfigurasi (`config.json`)

```json
{
  "port": 8765,
  "package": "",
  "screenshot_dirs": ["/sdcard/Pictures/Screenshots", "/sdcard/DCIM/Screenshots"]
}
```

| Kunci | Fungsi |
| --- | --- |
| `port` | Port lokal server. Ganti bila 8765 dipakai aplikasi lain. |
| `package` | Package name app yang diuji (mis. `com.example.app`) supaya versi build terbaca dari HP via adb. Kosong = isi manual. |
| `screenshot_dirs` | Folder Screenshots yang dipantau lewat adb. USB File transfer selalu memantau `DCIM/Screenshots` dan `Pictures/Screenshots`. |

---

## Troubleshooting

| Gejala | Penyebab | Solusi |
| --- | --- | --- |
| Windows: `Python was not found` atau Microsoft Store terbuka | Python asli belum terpasang | Install dari python.org dengan "Add to PATH", lalu buka ulang jendela |
| Mac: "cannot be opened because it is from an unidentified developer" | Gatekeeper | Klik kanan `run.command` → Open |
| Status: "HP tidak terhubung" | Kabel charging saja, atau mode USB belum dipilih | Pakai kabel data; pilih "File transfer" |
| Status: "terhubung tapi isi HP tidak terlihat" | Mode USB "Charging", atau HP terkunci | Buka kunci HP, pilih "File transfer" (kalau sudah: pilih "Charging" lalu "File transfer" lagi) |
| Status: "Membaca daftar screenshot… N dtk" lama | Pembacaan pertama setelah dicolok | Tunggu (bisa ±1 menit); screenshot yang diambil tetap masuk |
| Status: "folder Screenshots tidak ditemukan" | HP belum pernah membuat screenshot | Ambil satu screenshot di HP |
| Toast "Screenshot diabaikan: pilih TC dulu" | Belum ada TC aktif | Klik TC di daftar sebelum screenshot |
| Banner "adb tidak ditemukan" | platform-tools belum terpasang | Abaikan bila memakai File transfer; atau install platform-tools |
| `adb devices` kosong / `unauthorized` | Driver belum ada, atau dialog izin belum disetujui | Install driver vendor; cabut-colok lalu izinkan "Allow USB debugging" |
| Mac: HP hilang setelah tool ditutup paksa | Koneksi ke HP tidak tertutup | Cabut lalu colok lagi kabelnya |
| Port 8765 sudah dipakai | Aplikasi lain | Ganti `port` di `config.json` |

---

## Keamanan data

Folder `sessions/` berisi screenshot yang bisa memuat **data pribadi/sensitif** dari app yang diuji.

- Jangan di-commit (sudah di-gitignore) dan jangan dikirim keluar.
- Hapus session setelah dokumen di-upload.
- File Export `.zip` hanya dikirim lewat kanal yang aman.
- Server hanya bisa diakses dari laptop itu sendiri (`127.0.0.1`).

---

## Development

```sh
# Mac
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q
```

```bat
:: Windows
py -m venv .venv && .venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m pytest -q
```

Menjalankan server tanpa launcher: `python -m app.server --port 8765 [--sessions <folder>]`.

Struktur repo:

```text
run.command / run.bat   launcher per OS → launcher.py
launcher.py             buat .venv, install dependency, start server, buka browser
config.json             port, package, screenshot_dirs
app/
  server.py             HTTP server lokal + JSON API + UI statis
  tcsource.py           baca TC dari .xlsx
  session.py            state session + simpan ke disk
  sync.py               sinkron ulang Excel
  capture_auto.py       pilih adb atau USB File transfer
  capture_adb.py        capture via adb
  capture_mtp_mac.py    capture via USB File transfer (macOS, ImageCaptureCore)
  ptp.py                parser perintah PTP (dipakai versi Mac)
  capture_mtp_win.py    capture via USB File transfer (Windows, Explorer shell)
  docx_export.py        session → .docx
  results_export.py     teks Copy Result / Copy Remark
  archive.py            Export/Import session .zip
  static/               UI (HTML, JS, CSS tanpa build step)
docs/                   dokumentasi (cara kerja)
sessions/               data session (gitignored, bisa berisi data sensitif)
tests/                  pytest
```

## Lisensi

[MIT](LICENSE)
