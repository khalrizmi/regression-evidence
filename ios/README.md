# Regression Evidence — iOS

Tool lokal untuk mendokumentasikan hasil regresi/SIT manual di **iPhone**: screenshot masuk ke Test Case (TC) yang sedang dites, lalu dijadikan dokumen Word (`.docx`) dan hasilnya bisa disalin ke Google Sheet. Semua data tersimpan di komputer Anda.

## Cara kerjanya

1. Di komputer, **klik TC** yang sedang dites.
2. Di iPhone, ambil screenshot menggunakan shortcut **Ketuk punggung iPhone 2x** (Back Tap), terkirim lewat Wi-Fi ke TC tersebut.
4. Setelah semua TC selesai, klik **Generate DOCX**.

> Screenshot **tidak** terkirim otomatis; pengirimannya lewat ketukan di langkah 3 (Shortcut dari bagian 2).

## Yang dibutuhkan

- Mac atau Windows dengan **Python 3.10+** (Windows: saat install, centang *Add python.exe to PATH*).
- iPhone dan komputer di **Wi-Fi yang sama** (VPN dimatikan).
- File TC `.xlsx` dengan kolom `No TC`, `Function` / `Function Module`, `Scenario`, dan kolom `Result ...`.

## 1. Jalankan tool (di komputer)

**Mac**
1. Ekstrak zip, buka **Terminal**, ketik `cd ` lalu seret foldernya ke Terminal, Enter.
2. Jalankan: `xattr -dr com.apple.quarantine .` (sekali saja, supaya tidak diblokir Apple), lalu `python3 launcher.py`.
3. Lain kali cukup double-click **`run.command`**.

**Windows**
1. Ekstrak zip, double-click **`run.bat`**.
2. Saat dialog Firewall muncul, izinkan Python untuk **Private networks**.

Pertama kali jalan butuh internet ±1–2 menit. Setelah itu browser terbuka ke `http://127.0.0.1:8765`. **Jangan tutup jendela Terminal** selama dipakai.

Di layar awal: pilih file `.xlsx` → sheet → jenis dokumen (Regresi/SIT) → kolom **Result IOS** → versi build → **Mulai**.

## 2. Setup iPhone (sekali saja)

1. Di tool klik **Setup iPhone → Copy URL**, kirim ke iPhone (AirDrop/Notes).
2. **Tes koneksi:** buka URL itu di Safari iPhone, ganti kata `upload` dengan `ping`. Harus muncul `"Terhubung"`.
3. Buka app **Shortcuts → +**, beri nama `Evidence`, lalu tambah action berurutan:
   1. **Take Screenshot**
   2. **Get Contents of URL** → URL: tempel URL `upload` → *Show More* → Method **POST** → Request Body **File** → File: **Photos**
4. **Settings → Accessibility → Touch → Back Tap → Double Tap** → pilih `Evidence`.
5. Izinkan **Local Network** untuk Shortcuts (*Settings → Privacy & Security → Local Network*) dan akses **Foto** bila diminta.

## 4. Setelah selesai

| Tombol | Fungsi |
|---|---|
| **Preview Doc** | Lihat susunan dokumen sebelum dibuat |
| **Generate DOCX** | Buat dokumen di `sessions/<nama-session>/out/` |
| **Copy Result / Copy Remark** | Salin, lalu paste di Google Sheet pada sel pertama kolom yang ditunjukkan |
| **Sync Excel** | Gabungkan versi `.xlsx` terbaru tanpa menghapus screenshot/status |
| **Export / Import** | Pindahkan session antar komputer sebagai `.zip` |

Setelah dokumen di-upload, **hapus session** (screenshot bisa berisi data sensitif).

## Masalah umum

| Gejala | Solusi |
|---|---|
| "Apple could not verify run.command" (Mac) | Jalankan `xattr -dr com.apple.quarantine .` di folder tool, atau *System Settings → Privacy & Security → Open Anyway* |
| "The Internet connection appears to be offline" | Cek **Local Network** untuk Shortcuts, matikan VPN, pastikan Wi-Fi sama, lalu tes URL `ping` di Safari |
| Safari tidak bisa buka URL `ping` | Mac: klik **Allow** saat dialog Python muncul. Windows: set Wi-Fi ke **Private network** dan izinkan port (Command Prompt sebagai Administrator): `netsh advfirewall firewall add rule name="Regression Evidence iOS" dir=in action=allow protocol=TCP localport=8765 profile=private` |
| IP di URL salah | Ganti dengan IP Wi-Fi komputer (Mac: *Wi-Fi → Details*, Windows: `ipconfig`) |
| Wi-Fi kantor/guest tidak bisa | Gunakan hotspot HP; sebagian Wi-Fi memblokir antar-perangkat |
| "Pilih TC dulu" | Klik TC di daftar sebelum mengetuk |
| "Token salah" | Salin ulang URL dari **Setup iPhone** |
| "Format tidak didukung" | Yang terkirim bukan screenshot (PNG/JPEG), mis. foto HEIC |
| Yang terkirim screenshot sebelumnya | Tunggu thumbnail screenshot hilang sebelum mengetuk |
| Gambar dobel | Hapus yang berlebih dengan tombol **Hapus** di tool |
| Port 8765 terpakai | Ubah `port` di `config.json` |

## Keamanan

- Tampilan dan pengaturan tool hanya bisa dibuka dari komputer itu sendiri.
- Yang terbuka di jaringan hanya penerima screenshot, dan wajib **token** rahasia di `config.json`. Jangan dibagikan atau di-commit.
- Gunakan Wi-Fi tepercaya.

## Untuk developer

```
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q          # tes
.venv/bin/python -m app.server         # jalankan server
```

`app/server.py` (HTTP + upload) · `tcsource.py` (baca xlsx) · `session.py` · `docx_export.py` · `sync.py` · `archive.py` · `results_export.py` · `static/` (UI) · `tests/`.
`session.py`, `docx_export.py`, `sync.py`, `archive.py` identik dengan versi Android (skema `state.json` dan format dokumen sama). Sheet yang No TC-nya dinomori ulang per bagian otomatis diberi nama modul sebagai pembeda.
