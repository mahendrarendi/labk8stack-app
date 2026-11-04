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

Semua manifest challenge memakai `LAB_APP_IMAGE`. Default-nya masih placeholder
`ghcr.io/change-me-owner/k8s-lab-app`, yang **tidak akan bisa di-pull** sampai
Anda push image sendiri.

```bash
cd ../k8s-lab-app
docker build -t ghcr.io/<owner>/k8s-lab-app:0.1.0 .
docker push ghcr.io/<owner>/k8s-lab-app:0.1.0
```

Lalu set:

```bash
LAB_APP_IMAGE=ghcr.io/<owner>/k8s-lab-app:0.1.0
LAB_APP_GOOD_TAGS=0.1.0,v0.1.0,latest
```

`LAB_APP_GOOD_TAGS` adalah daftar tag yang dianggap valid oleh challenge
`image-pull-backoff`.

### Menguji tanpa image sendiri

Sebelum image `k8s-lab-app` tersedia, mode cluster masih bisa dipakai dengan
image HTTP generik. Yang diuji di sini: `registry.k8s.io/echoserver:1.10`
(listen di `:8080`, semua path termasuk `/healthz` membalas 200):

```bash
LAB_APP_IMAGE=registry.k8s.io/echoserver:1.10 \
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

Di luar cluster:

```bash
LAB_MODE=kubernetes \
LAB_KUBECONFIG=$HOME/.kube/config \
LAB_APP_IMAGE=ghcr.io/<owner>/k8s-lab-app:0.1.0 \
LAB_APP_GOOD_TAGS=0.1.0,v0.1.0,latest \
SECRET_KEY="$(openssl rand -hex 32)" \
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Atau dengan Docker Compose (mount kubeconfig read-only):

```bash
LAB_MODE=kubernetes LAB_KUBECONFIG=/kube/config \
LAB_APP_IMAGE=ghcr.io/<owner>/k8s-lab-app:0.1.0 \
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

Setiap attempt baru ("Retry" atau attempt pertama untuk sebuah challenge)
menghapus **resource di dalam** namespace, bukan namespace-nya. Namespace,
ServiceAccount `lab-runner`, dan RoleBinding tetap ada, jadi reset selesai
dalam hitungan detik. Ini juga menghindari namespace yang tertahan di
`Terminating` (di cluster uji, penghapusan namespace butuh beberapa menit
karena controller-manager mengejar cache yang tertinggal).

Kalau sebuah namespace sudah terlanjur `Terminating`, platform menunggu
maksimal ~2 menit sebelum memberi pesan error; resource lain (challenge lain)
tetap berjalan normal karena namespace-nya terpisah.

## 7. Bersihkan

```bash
# resource user
kubectl delete namespace -l k8s-lab.io/component=sandbox

# RBAC platform (opsional)
kubectl delete -f deploy/rbac.yaml
```

## Catatan keamanan

- **Jangan** set `LAB_IMPERSONATE=false` di produksi: semua user akan berjalan
  dengan identitas platform (kalau kubeconfig-nya admin, itu cluster-admin).
- Platform butuh hak `impersonate`; siapa pun yang memegang kredensial platform
  bisa menjadi ServiceAccount user mana pun. Perlakukan seperti kredensial
  admin.
- Terminal memblokir flag yang bisa mengganti namespace/identitas
  (`-n`, `-A`, `--as`, `--kubeconfig`, `--server`, `--token`, ...) dan shell
  operator (`;`, `|`, `&&`, `$()`, ...).
- Role `lab-user` sengaja tidak memberi `pods/exec`, `port-forward`, akses node,
  RBAC, atau CRD.
- Tambahkan `ResourceQuota`/`LimitRange` per namespace bila cluster dipakai
  bersama beban lain. Contoh:

```yaml
apiVersion: v1
kind: ResourceQuota
metadata:
  name: lab-quota
spec:
  hard:
    pods: "20"
    requests.cpu: "4"
    requests.memory: 8Gi
    services: "10"
```

- Beberapa cluster memasang Pod Security Admission `restricted` secara global.
  Pod challenge akan memberi warning (atau ditolak bila `enforce`). Longgarkan
  label PSA untuk namespace `lab-u*` bila perlu, misalnya:

```bash
kubectl label namespace lab-u<id>-<slug> \
  pod-security.kubernetes.io/enforce=baseline --overwrite
```

## Troubleshooting

| Gejala | Penyebab umum |
|---|---|
| `forbidden: cannot impersonate resource` | ClusterRole `lab-provisioner` belum di-bind ke identitas platform |
| `forbidden` saat membuat RoleBinding | provisioner tidak punya `bind` pada ClusterRole `lab-user` |
| `ImagePullBackOff` di semua challenge | `LAB_APP_IMAGE` masih placeholder atau node tidak bisa akses registry |
| `namespace is being terminated` | jarang terjadi; platform menunggu namespace selesai dihapus sebelum dipakai lagi |
| Reset terasa lama | namespace baru sedang dibuat; setelah attempt pertama, reset hanya menghapus resource (cepat) |
| `LAB_MODE=kubernetes but the 'kubectl' binary is missing` | install `kubectl` di image/container platform |
| Pod ditolak dengan pesan PodSecurity | namespace butuh label PSA yang lebih longgar |
