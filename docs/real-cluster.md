# Menghubungkan platform ke cluster Kubernetes asli

Panduan ini menjelaskan cara menjalankan `LAB_MODE=kubernetes`, termasuk kenapa
ServiceAccount diperlukan dan bagaimana isolasi antar-user bekerja.

> Sudah diuji pada cluster Talos v1.14.1 / Kubernetes v1.36.0 (3 control-plane,
> 2 worker) memakai `talosctl kubeconfig` dan `--as` per user.

## Ringkasan

Platform **tidak** mengirim kubeconfig ke browser. Semua perintah `kubectl`
dijalankan di server. Jadi secara teknis platform hanya butuh satu identitas
yang bisa membuat namespace. Tetapi untuk lab multi-user, jangan pakai
cluster-admin. Gunakan model:

```
platform (lab-provisioner)
   │  membuat / menghapus namespace lab-u<id>-<slug>
   │  membuat ServiceAccount lab-runner + RoleBinding
   ▼
namespace lab-u<id>-<slug>
   ├── ServiceAccount lab-runner  ── bound to ── ClusterRole lab-user
   └── Deployment/Service milik challenge
        ▲
        │  platform menjalankan kubectl --as=system:serviceaccount:lab-u<id>-<slug>:lab-runner
        │
      user (lewat editor/terminal web)
```

Dengan model ini:

- user tidak bisa melihat namespace lain, node, RBAC, atau Secret cluster;
- setiap perintah tercatat di audit log atas nama ServiceAccount user tersebut;
- menghapus namespace = menghapus seluruh sisa resource user.

## 1. Ambil kubeconfig

Untuk Talos:

```bash
talosctl -n 10.87.44.20 kubeconfig ~/.kube/config
kubectl get nodes
```

Untuk cluster lain, pakai kubeconfig yang sudah ada, misalnya
`/etc/kubernetes/admin.conf`, atau buat user khusus:

```bash
kubectl create serviceaccount lab-provisioner -n k8s-lab-system
```

## 2. Terapkan RBAC

File `deploy/rbac.yaml` berisi:

- Namespace `k8s-lab-system` + ServiceAccount `lab-provisioner`;
- ClusterRole `lab-user` — hak peserta, hanya resource namespaced;
- ClusterRole `lab-provisioner` — mengelola namespace, ServiceAccount,
  RoleBinding, dan boleh `impersonate` ServiceAccount;
- ClusterRoleBinding untuk `lab-provisioner`.

```bash
kubectl apply -f deploy/rbac.yaml
```

Cek:

```bash
kubectl get clusterrole lab-user lab-provisioner
kubectl -n k8s-lab-system get sa
```

## 3. Siapkan image aplikasi

Semua manifest challenge memakai `LAB_APP_IMAGE` dari
`k8s-lab-platform/.env` — satu-satunya tempat konfigurasi image. Default di
kode hanyalah placeholder `ghcr.io/change-me-owner/k8s-lab-app`, yang **tidak
akan bisa di-pull** sampai Anda push image sendiri.

```bash
cd ../k8s-lab-app
docker build -t ghcr.io/<owner>/k8s-lab-app:0.1.0 .
docker push ghcr.io/<owner>/k8s-lab-app:0.1.0
```

Lalu edit `k8s-lab-platform/.env`:

```env
LAB_APP_IMAGE=ghcr.io/<owner>/k8s-lab-app:0.1.0
LAB_APP_GOOD_TAGS=0.1.0,v0.1.0,latest
```

`LAB_APP_GOOD_TAGS` adalah daftar tag yang dianggap valid oleh challenge
`image-pull-backoff`.

### Menguji tanpa image sendiri

Sebelum image `k8s-lab-app` tersedia, mode cluster masih bisa dipakai dengan
image HTTP generik. Yang diuji di sini: `registry.k8s.io/echoserver:1.10`
(listen di `:8080`, semua path termasuk `/healthz` membalas 200).
Set di `.env`:

```env
LAB_APP_IMAGE=registry.k8s.io/echoserver:1.10
LAB_APP_GOOD_TAGS=1.10,latest
```

| Challenge | Gejala terlihat di cluster? | Catatan |
|---|---|---|
| `image-pull-backoff` | ✅ `ErrImagePull` | tag `0.0.3` tidak ada di repo image |
| `service-no-endpoints` | ✅ Service tanpa endpoint | |
| `pending-resources` | ✅ pod `Pending` | request CPU/memory melebihi node |
| `command-override` | ✅ `RunContainerError` | |
| `oom-killed` | ❌ | echoserver tidak memakai 32Mi; pod tetap `Running` |
| `readiness-probe` | ❌ | echoserver membalas 200 untuk path apa pun, jadi probe `/health` tetap lolos |
| `port-env` | ❌ | echoserver mengabaikan env `PORT` |
| `service-target-port` | ✅ endpoint di `:8081` | |
| `deployment-selector` | ✅ apply ditolak API server | |
| `deployment-paused` | ✅ tidak ada pod | |

Validator tetap memeriksa spec, jadi tiga challenge yang gejalanya tidak
muncul **masih bisa "lulus"** bila diperbaiki dengan benar. Tetapi untuk demo
yang jujur, pakai image `k8s-lab-app` asli: aplikasi itu membaca `PORT`,
menyajikan `/healthz`, dan cukup berat untuk benar-benar OOM di 32Mi.

## 4. Jalankan platform

Di luar cluster (image & `SECRET_KEY` sudah ada di `.env`):

```bash
LAB_MODE=kubernetes \
LAB_KUBECONFIG=$HOME/.kube/config \
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Atau dengan Docker Compose (image dari `.env`, mount kubeconfig read-only):

```bash
LAB_MODE=kubernetes LAB_KUBECONFIG=/kube/config \
docker compose up -d
```

Saat platform berjalan sebagai Pod di cluster, kubeconfig tidak perlu: Pod
otomatis memakai ServiceAccount-nya. Gunakan `lab-provisioner` sebagai
`serviceAccountName`, dan `--as` tetap bekerja karena RBAC-nya sudah diberi
`impersonate`.

## 5. Verifikasi isolasi

Buka platform, masuk dengan nama apa pun, lalu buka satu challenge. Platform
akan membuat `lab-u<id>-<slug>` beserta ServiceAccount-nya.

```bash
kubectl get ns | grep lab-u
kubectl -n lab-u<id>-<slug> get sa,rolebinding
kubectl -n lab-u<id>-<slug> get pods

# peserta hanya boleh menyentuh namespace-nya sendiri
kubectl auth can-i get pods -n lab-u<id>-<slug> --as=system:serviceaccount:lab-u<id>-<slug>:lab-runner   # yes
kubectl auth can-i get pods -n default --as=system:serviceaccount:lab-u<id>-<slug>:lab-runner     # no
kubectl auth can-i get secrets -n kube-system --as=system:serviceaccount:lab-u<id>-<slug>:lab-runner # no
```

## 6. Retry dan reset

Reset pada attempt aktif menghapus resource challenge di dalam namespace yang
sama. Setelah lulus atau waktu habis, namespace dihapus secara asinkron. Retry
langsung setelah lulus dapat menunggu hingga namespace lama selesai berstatus
`Terminating` sebelum namespace baru dibuat.

Kalau sebuah namespace sudah terlanjur `Terminating`, platform menunggu
maksimal ~2 menit sebelum memberi pesan error; resource lain (challenge lain)
tetap berjalan normal karena namespace-nya terpisah.

## 7. Bersihkan

Namespace yang ditinggalkan diberi anotasi `k8s-lab.io/expires-at` pada saat
setup. Cleanup menghapusnya setelah durasi challenge ditambah grace period,
meski user masih aktif di challenge lain. Untuk namespace lama tanpa anotasi,
cleanup tetap memakai `users.last_seen`; namespace dari attempt yang sudah
selesai ikut dibersihkan. Platform di luar cluster menjalankan cleanup setiap
15 menit secara default. Deployment di cluster mematikannya karena memakai
`deploy/cleanup-cronjob.yaml`.

```bash
# Dry-run: tampilkan user idle dan namespace yang akan dihapus.
LAB_MODE=kubernetes LAB_KUBECONFIG=$HOME/.kube/config \
  .venv/bin/python -m app.cleanup --idle-days 30

# Benar-benar hapus (namespace dihapus asinkron, --wait=false).
LAB_MODE=kubernetes LAB_KUBECONFIG=$HOME/.kube/config \
  .venv/bin/python -m app.cleanup --idle-days 30 --apply
```

Opsi lain:

| Flag | Arti |
|---|---|
| `--idle-days N` | batas idle; default dari `LAB_SANDBOX_IDLE_DAYS` (30) |
| `--all` | pilih semua user, bukan hanya yang idle |
| `--keep-orphans` | jangan hapus namespace yang user-nya sudah tidak ada di database |
| `--apply` | eksekusi; tanpa ini hanya dry-run |

Contoh cron harian pukul 03:00:

```cron
0 3 * * * cd /root/k8s-lab/k8s-lab-platform && \
  LAB_MODE=kubernetes LAB_KUBECONFIG=/root/.kube/talos-lab.conf \
  .venv/bin/python -m app.cleanup --idle-days 30 --apply \
  >> /var/log/k8s-lab-cleanup.log 2>&1
```

Untuk menjalankan platform dan cleanup secara otomatis di cluster:

1. Bangun dan push image platform serta image aplikasi challenge. Ganti image
   placeholder di `deploy/platform.yaml` dan `deploy/cleanup-cronjob.yaml`.
2. Siapkan PostgreSQL dengan backup terjadwal. Simpan `SECRET_KEY`,
   `DATABASE_URL`, `LAB_APP_IMAGE`, dan `LAB_APP_GOOD_TAGS` dalam file privat
   format `KEY=value`, lalu buat Secret:

   ```bash
   kubectl -n k8s-lab-system create secret generic k8s-lab-platform-config \
     --from-env-file=/path/to/private-platform.env
   ```

3. Terapkan manifest dan cek status:

   ```bash
   kubectl apply -f deploy/platform.yaml
   kubectl apply -f deploy/cleanup-cronjob.yaml
   kubectl -n k8s-lab-system rollout status deployment/k8s-lab-platform
   kubectl -n k8s-lab-system port-forward svc/k8s-lab-platform 8080:80
   ```

   `/readyz` memeriksa koneksi database; `/metrics` menyediakan jumlah attempt
   berdasarkan status dalam format Prometheus. Edit host dan TLS Secret pada
   `deploy/ingress.example.yaml` bila memakai ingress controller. Jangan membuka
   UI ke internet sebelum menambahkan rate limit login dan perlindungan CSRF.
   Untuk backup manual atau terjadwal, jalankan
   `DATABASE_URL=... BACKUP_DIR=/path/backup bash scripts/backup-postgres.sh`.
   Letakkan backup di penyimpanan yang tahan terhadap kehilangan cluster dan
   uji pemulihannya secara berkala.

Untuk menghapus semua sandbox sekaligus (misalnya sebelum kelas baru):

```bash
# cara paling agresif, tanpa melihat database
kubectl delete namespace -l k8s-lab.io/component=sandbox

# RBAC platform (opsional)
kubectl delete -f deploy/rbac.yaml
```

## Catatan keamanan

- Mode cluster menolak `LAB_IMPERSONATE=false` agar perintah peserta selalu
  memakai identitas ServiceAccount namespace.
- Platform hanya diberi hak `impersonate` ServiceAccount bernama `lab-runner`.
  Kredensial platform tetap sensitif karena bisa membuat namespace dan
  mengubah RoleBinding di dalamnya. Jika `LAB_RUNNER_SA` diganti, sesuaikan
  `resourceNames` dalam `deploy/rbac.yaml`.
- Terminal memblokir flag yang bisa mengganti namespace/identitas
  (`-n`, `-A`, `--as`, `--kubeconfig`, `--server`, `--token`, ...) dan shell
  operator (`;`, `|`, `&&`, `$()`, ...).
- Role `lab-user` sengaja tidak memberi `pods/exec`, `port-forward`, akses node,
  RBAC, atau CRD.
- Platform memasang `ResourceQuota`, `LimitRange`, NetworkPolicy ingress, dan
  label Pod Security Admission `baseline` saat membuat namespace. Kuota request
  sengaja lebih tinggi dari manifest rusak `pending-resources` agar Pod tetap
  bisa dibuat dan menunjukkan gejala `Pending`.

- Beberapa cluster menerapkan Pod Security Admission `restricted` lewat
  kebijakan lain. Manifest challenge belum memenuhi level tersebut. Namespace
  lab meminta `baseline`; pastikan kebijakan cluster menerima label ini.

## Troubleshooting

| Gejala | Penyebab umum |
|---|---|
| `forbidden: cannot impersonate resource` | ClusterRole `lab-provisioner` belum di-bind ke identitas platform |
| `forbidden` saat membuat RoleBinding | provisioner tidak punya `bind` pada ClusterRole `lab-user` |
| `ImagePullBackOff` di semua challenge | `LAB_APP_IMAGE` di `.env` masih placeholder atau node tidak bisa akses registry |
| `namespace is being terminated` | jarang terjadi; platform menunggu namespace selesai dihapus sebelum dipakai lagi |
| Reset terasa lama | namespace baru sedang dibuat; setelah attempt pertama, reset hanya menghapus resource (cepat) |
| `LAB_MODE=kubernetes but the 'kubectl' binary is missing` | install `kubectl` di image/container platform |
| Pod ditolak dengan pesan PodSecurity | kebijakan cluster menegakkan level lebih ketat dari `baseline` |
