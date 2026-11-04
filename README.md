# k8s-lab-platform

Platform latihan Kubernetes bergaya KodeKloud: user memasukkan nama, memilih
challenge, lalu **memperbaiki manifest yang sengaja dirusak** lewat editor YAML
di browser. Ada timer, skor, dan leaderboard 7 hari.

Contoh challenge: pod `ImagePullBackOff` karena tag image salah, Service tanpa
Endpoint karena selector keliru, dan pod `Pending` karena resource request
terlalu besar.

## Alur

```
buka web → isi nama → pilih challenge
   → server membuat sandbox + manifest rusak, timer mulai
   → user edit YAML → Apply (lihat status ala kubectl) → Verifikasi
   → lulus: skor = f(sisa waktu, jumlah percobaan)
   → leaderboard 7 hari terakhir
```

## Fitur (MVP)

- Login **nama saja** (cookie bertanda tangan, tanpa password)
- **UI berbahasa Inggris**
- 3 challenge nyata dengan validator masing-masing
- **Dua cara menyelesaikan**, bisa dicampur karena berbagi satu sandbox:
  - **Manifest editor** — edit YAML, Apply, lihat output ala `kubectl`
  - **Terminal** — imperative: `kubectl get/describe/logs`, `set image`, `scale`, `rollout status`, `delete pod`, `create deployment`
- Timer per attempt, skor menurun seiring waktu & percobaan
- Leaderboard **rolling 7 hari**
- Dua mode engine: `fake` (simulator) dan `kubernetes` (kubectl asli)

## Menjalankan di lokal

```bash
make setup      # buat .venv + install dependensi
make test       # 16 test
make run        # http://localhost:8080
```

Atau manual:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/uvicorn app.main:app --reload --port 8080
```

### Docker Compose

```bash
cp .env.example .env
docker compose up -d --build
open http://localhost:8080
```

SQLite disimpan di volume `platform-data`, jadi skor tidak hilang saat restart.

## Mode engine

| `LAB_MODE` | Perilaku | Butuh cluster? |
|---|---|---|
| `fake` (default) | Output `kubectl` disimulasikan dari isi YAML | Tidak |
| `kubernetes` | Menjalankan `kubectl apply` di namespace user | Ya |

Di mode `kubernetes`, setiap user mendapat namespace **`lab-u<id>`**. Platform
membuat namespace itu, menerapkan manifest rusak, lalu memverifikasi hasilnya.
Artinya ServiceAccount platform butuh izin membuat namespace dan resource di
dalamnya — **jangan pakai cluster produksi**.

> Status saat ini: mode `fake` sudah lengkap dan teruji. Mode `kubernetes`
> sudah diimplementasikan tetapi belum diuji karena belum ada kubeconfig aktif
> di mesin pengembangan.

## Challenge

| Slug | Gejala | Yang diperbaiki | Poin |
|---|---|---|---|
| `image-pull-backoff` | `ImagePullBackOff` | Tag image salah | 100 |
| `service-no-endpoints` | 503 / `<none>` | `selector` Service | 120 |
| `pending-resources` | Pod `Pending` | `resources.requests` | 150 |

Menambah challenge = menambah satu entri di `app/challenges.py`: manifest rusak,
fungsi `validate(doc)`, dan `simulate(doc)` untuk mode fake.

## Skor

```
skor = poin × (0.5 + 0.5 × sisa_waktu/ durasi) − 10 × (percobaan − 1)
minimum 20
```

Lulus cepat dan tanpa salah percobaan = skor maksimal. Leaderboard menjumlahkan
skor attempt `passed` dalam 7 hari terakhir; seri dipecah oleh waktu penyelesaian
lebih awal.

## Struktur

```
app/
├── main.py          # route halaman + API (apply/verify/terminal/reset)
├── db.py            # SQLite: users, attempts, leaderboard
├── challenges.py    # katalog challenge + validator + simulasi
├── sandbox.py       # state in-memory, dipakai editor & terminal
├── terminal.py      # interpreter perintah kubectl (mode fake)
├── scoring.py       # rumus skor
├── security.py      # cookie sesi
├── engine/
│   ├── fake.py      # simulator
│   └── kubernetes.py# kubectl asli per namespace
├── templates/       # halaman server-rendered (English UI)
└── static/          # CSS + JS (tanpa framework)
tests/               # pytest (16 test)
```

## Terminal

Perintah yang didukung di mode `fake` (semuanya mengubah sandbox yang sama
dengan editor):

```
kubectl get pods|deploy|svc|ep
kubectl describe pod|deployment|service <name>
kubectl logs <pod>
kubectl set image deployment/web <container>=<image>
kubectl scale deployment/web --replicas=<n>
kubectl rollout status deployment/web
kubectl delete pod <name>        # pod dibuat ulang oleh Deployment
kubectl create deployment <name> --image=<image>
help
```

Contoh menyelesaikan `ImagePullBackOff` tanpa editor:

```
$ kubectl get pods
web-6d8f7c9b5d-abc12   0/1   ImagePullBackOff   0   4m
$ kubectl set image deployment/web web=ghcr.io/change-me-owner/k8s-lab-app:0.1.0
deployment.apps/web image updated
$ kubectl get pods
web-6d8f7c9b5d-abc12   1/1   Running   0   4m
```

Di mode `kubernetes`, perintah dijalankan sebagai `kubectl` sungguhan di
namespace user. Flag `-n/--namespace/-A`, shell operator, dan verb di luar
daftar aman ditolak.

## Keamanan & batasan

- Login hanya nama: **tidak ada autentikasi kuat**, cocok untuk lab internal.
- Cookie ditandatangani `SECRET_KEY`; ganti nilai default sebelum dipakai publik.
- Mode `kubernetes` memberi platform izin ke cluster. Isolasi per user baru
  sebatas namespace — belum ada NetworkPolicy/RBAC ketat.
- Terminal mode `fake` lengkap; mode `kubernetes` memakai kubectl asli (belum diuji).
- Leaderboard memakai SQLite; untuk multi-replica perlu Postgres.

## Roadmap

1. Upgrade terminal ke PTY penuh (xterm.js + WebSocket + `kubectl exec`) agar
   bisa menjalankan shell interaktif, bukan hanya perintah satu baris.
2. Verifikasi berbasis kondisi cluster (bukan hanya isi YAML).
3. Kubeconfig/token sementara per user untuk `kubectl` dari terminal sendiri.
4. Reset otomatis namespace kedaluwarsa (cron/TTL).
5. Postgres + leaderboard per-minggu kalender.
