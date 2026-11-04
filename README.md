# k8s-lab-platform

Platform latihan Kubernetes bergaya KodeKloud: user memasukkan nama, memilih
challenge, lalu **memperbaiki manifest yang sengaja dirusak** lewat editor YAML
di browser. Ada timer, skor, dan leaderboard 7 hari.

10 challenge dengan tingkat kesulitan Easy/Medium/Hard. UI hanya menampilkan
gejala (pod tidak ready, Service 503, rollout macet, dan seterusnya) — penyebab
dan perbaikannya tidak pernah ditulis, jadi user harus melakukan troubleshooting
sendiri lewat `kubectl`.

## Alur

```
buka web → isi nama → pilih challenge
   → server membuat sandbox + manifest rusak, timer mulai
   → user edit YAML → Apply (lihat status ala kubectl) → Verifikasi
   → lulus: skor = f(sisa waktu, jumlah percobaan)
   → leaderboard 7 hari terakhir
```

## Fitur (MVP)

- Login **username + password** (cookie bertanda tangan); username yang sama
  mempertahankan skor di leaderboard
- **UI berbahasa Inggris**
- **10 challenge** nyata dengan validator masing-masing
- **Dua cara menyelesaikan**, bisa dicampur karena berbagi satu sandbox:
  - **Manifest editor** — edit YAML, Apply, lihat output ala `kubectl`
  - **Terminal** — imperative: `kubectl get/describe/logs`, `set image`, `set env`, `set resources`, `scale`, `rollout status|resume|pause`, `delete pod`, `create deployment`
- Timer per attempt, skor menurun seiring waktu & percobaan
- Leaderboard **rolling 7 hari**
- Dua mode engine: `fake` (simulator) dan `kubernetes` (kubectl asli per namespace)

## Mode cluster asli (`LAB_MODE=kubernetes`)

Panduan lengkap: [`docs/real-cluster.md`](docs/real-cluster.md).

Singkatnya:

```bash
kubectl apply -f deploy/rbac.yaml          # SA + ClusterRole
LAB_MODE=kubernetes \
LAB_KUBECONFIG=$HOME/.kube/config \
LAB_APP_IMAGE=ghcr.io/<owner>/k8s-lab-app:0.1.0 \
.venv/bin/uvicorn app.main:app --port 8080
```

Setiap user mendapat namespace `lab-u<id>-<slug>`, ServiceAccount `lab-runner`, dan
RoleBinding ke ClusterRole `lab-user`. Semua perintah dijalankan dengan
`kubectl --as=system:serviceaccount:lab-u<id>-<slug>:lab-runner`, jadi RBAC cluster
yang menegakkan isolasi — bukan kode aplikasi. Jangan pakai kubeconfig
cluster-admin tanpa impersonation di produksi.

## Menjalankan di lokal

```bash
make setup      # buat .venv + install dependensi
make test       # 33 test
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

## Akun & leaderboard

- **Create account**: username + password (minimal 6 karakter) membuat akun baru.
- **Sign in**: username yang sama + password memulihkan akun, attempt, dan skor
  yang sudah ada di leaderboard. Username tidak membedakan huruf besar/kecil.
- Password disimpan sebagai hash **PBKDF2-HMAC-SHA256** (600.000 iterasi, salt
  acak per user), bukan teks biasa.
- Tidak ada reset password lewat email. Bila password hilang, hapus user lewat
  SQLite atau minta admin membuatkan akun baru.
- User lama dari versi "nama saja" tidak punya password. Pemiliknya bisa
  memakai **Create account** dengan username yang sama untuk mengklaim akun
  itu satu kali.

## Mode engine

| `LAB_MODE` | Perilaku | Butuh cluster? |
|---|---|---|
| `fake` (default) | Output `kubectl` disimulasikan dari isi YAML | Tidak |
| `kubernetes` | Menjalankan `kubectl apply` di namespace user | Ya |

Di mode `kubernetes`, setiap user mendapat namespace **`lab-u<id>-<slug>`**. Platform
membuat namespace itu, menerapkan manifest rusak, lalu memverifikasi hasilnya.
Artinya ServiceAccount platform butuh izin membuat namespace dan resource di
dalamnya — **jangan pakai cluster produksi**.

> Status saat ini: mode `fake` sudah lengkap dan teruji. Mode `kubernetes`
> sudah diimplementasikan tetapi belum diuji karena belum ada kubeconfig aktif
> di mesin pengembangan.

## Challenge

| Slug | Judul di UI | Gejala yang ditampilkan | Penyebab (spoiler) | Poin |
|---|---|---|---|---|
| `image-pull-backoff` | Deployment never becomes ready | Pod tidak pernah ready | Tag image tidak ada di registry | 100 |
| `service-no-endpoints` | Service returns 503 | Service selalu 503 | `selector` Service tidak cocok dengan label pod | 110 |
| `pending-resources` | Pod never schedules | Pod tetap `Pending` | `resources.requests` melebihi kapasitas node | 140 |
| `command-override` | Container restarts in a loop | Pod restart terus | `command` menimpa entrypoint image | 110 |
| `oom-killed` | Container is killed while starting | Container mati saat boot | `limits.memory` terlalu kecil | 140 |
| `readiness-probe` | Pods never become Ready | Pod Running tapi 0 ready | `readinessProbe` menunjuk path salah | 130 |
| `port-env` | Application unreachable after config change | Container hidup tapi tidak ready | `PORT` beda dengan port probe | 130 |
| `service-target-port` | Service connections are refused | Endpoint ada, koneksi gagal | `targetPort` salah | 130 |
| `deployment-selector` | Deployment cannot be applied | Apply ditolak, tidak ada pod | `selector` != label template | 150 |
| `deployment-paused` | Rollout never progresses | Tidak ada pod sama sekali | `spec.paused: true` | 150 |

UI hanya menampilkan gejala. Jangan pernah menambahkan penyebab, nama field yang
salah, atau nilai pengganti ke `summary` / `story` / `objective` / `hint`,
karena itu menghilangkan bagian troubleshoot dari latihan.

Menambah challenge = menambah satu entri di `app/challenges.py`: manifest rusak,
fungsi `validate(doc)`, `simulate(doc)` untuk mode fake, plus `status` /
`pod_events` / `pod_logs` agar simulator terminal ikut berperilaku benar.

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
│   └── kubernetes.py# kubectl asli per namespace + impersonation
├── templates/       # halaman server-rendered (English UI)
└── static/          # CSS + JS (tanpa framework)
deploy/rbac.yaml     # ServiceAccount + ClusterRole untuk mode cluster
docs/real-cluster.md # panduan connect ke cluster asli
tests/               # pytest (33 test)
```

## Terminal

Perintah yang didukung di mode `fake` (semuanya mengubah sandbox yang sama
dengan editor):

```
kubectl get pods|deploy|svc|ep
kubectl describe pod|deployment|service <name>
kubectl logs <pod>
kubectl set image deployment/web <container>=<image>
kubectl set env deployment/web <KEY>=<value>
kubectl set resources deployment/web --limits=cpu=500m,memory=256Mi
kubectl scale deployment/web --replicas=<n>
kubectl rollout status|resume|pause deployment/web
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

- Autentikasi memakai username + password (hash PBKDF2), tapi belum ada
  verifikasi email, reset password, atau rate limiting login. Cocok untuk lab
  internal.
- Cookie ditandatangani `SECRET_KEY`; ganti nilai default sebelum dipakai publik.
  Jalankan di belakang HTTPS agar password tidak dikirim dalam teks biasa.
- Mode `kubernetes` memberi platform izin ke cluster. Isolasi per user ditegakkan
  RBAC + impersonation per namespace (lihat `docs/real-cluster.md`).
- Terminal mode `fake` lengkap; mode `kubernetes` memakai kubectl asli.
- Leaderboard memakai SQLite; untuk multi-replica perlu Postgres.

## Roadmap

1. Upgrade terminal ke PTY penuh (xterm.js + WebSocket + `kubectl exec`) agar
   bisa menjalankan shell interaktif, bukan hanya perintah satu baris.
2. Verifikasi berbasis kondisi cluster (bukan hanya isi YAML).
3. Kubeconfig/token sementara per user untuk `kubectl` dari terminal sendiri.
4. Reset otomatis namespace kedaluwarsa (cron/TTL).
5. Postgres + leaderboard per-minggu kalender.
