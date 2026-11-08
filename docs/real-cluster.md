# Connect LabK8stack to a Kubernetes cluster

This guide covers the legacy standalone manifests in `deploy/`. For the
maintained Helm and Argo CD deployment, use the sibling `../k8s-lab-deploy/`
repository. Run public, untrusted labs in a dedicated training cluster.

## Identity model

The platform creates one namespace per user/challenge pair. It creates a
`lab-runner` ServiceAccount and RoleBinding in that namespace. Learner
commands run with `kubectl --as=system:serviceaccount:<namespace>:lab-runner`,
so Kubernetes RBAC enforces the runner's permissions. The browser never
receives a kubeconfig or ServiceAccount token.

The platform's own identity needs permission to create and delete lab
namespaces, create sandbox resources, bind the `lab-user` ClusterRole, and
impersonate `lab-runner`. The supplied `deploy/rbac.yaml` grants those
permissions to `lab-provisioner` for the standalone deployment. Review its
scope before using it outside a dedicated lab cluster.

## 1. Prepare cluster access

Use a kubeconfig that reaches the target cluster. For example:

```bash
kubectl get nodes
kubectl get --raw=/version
```

Install the standalone RBAC resources:

```bash
kubectl apply -f deploy/rbac.yaml
kubectl get clusterrole lab-user lab-provisioner
kubectl -n k8s-lab-system get serviceaccount lab-provisioner
```

## 2. Configure the application

Create `.env` from `.env.example`. Set `SECRET_KEY`, `DATABASE_URL`,
`LAB_MODE=kubernetes`, and a real image for challenge workloads. The default
`registry.k8s.io/echoserver:1.10` is useful for a basic connectivity test, but
it does not reproduce every challenge symptom: it ignores `PORT`, responds to
any HTTP path, and may not be killed by the OOM challenge's memory limit.

When running the platform on a machine outside Kubernetes, point it at a
kubeconfig and start the server:

```bash
LAB_MODE=kubernetes LAB_KUBECONFIG=$HOME/.kube/config \
  .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8080
```

The Docker Compose setup can use a mounted kubeconfig at `/kube/config`:

```bash
LAB_MODE=kubernetes LAB_KUBECONFIG=/kube/config docker compose up -d
```

For a Pod deployment, use the Helm chart in `../k8s-lab-deploy/`. It mounts an
explicit in-cluster kubeconfig whose `tokenFile` points to the projected
ServiceAccount token. This is necessary because `kubectl --as` can prevent the
automatic in-cluster configuration fallback. Do not place a long-lived token
in a ConfigMap or commit one to Git.

The files `deploy/platform.yaml` and `deploy/cleanup-cronjob.yaml` are legacy
examples with placeholder images. They do not include the Helm chart's
kubeconfig mount and need that configuration before they can run impersonated
commands reliably.

## 3. Check sandbox isolation

Open a challenge in the web UI, then inspect its namespace. Replace the sample
name below with the namespace created by your attempt:

```bash
kubectl get namespaces -l k8s-lab.io/component=sandbox
kubectl -n lab-u1-image-pull-backoff get serviceaccount,rolebinding,pods
```

Check that the runner can read Pods in its own namespace and cannot read
Secrets in another namespace:

```bash
kubectl auth can-i get pods -n lab-u1-image-pull-backoff --as=system:serviceaccount:lab-u1-image-pull-backoff:lab-runner
kubectl auth can-i get secrets -n kube-system --as=system:serviceaccount:lab-u1-image-pull-backoff:lab-runner
```

The first command should return `yes`; the second should return `no`. The
namespace suffix and user ID must match a sandbox that exists in your cluster.

## 4. Clean up abandoned sandboxes

The application annotates each namespace with `k8s-lab.io/expires-at`. The
cleanup job removes expired namespaces, namespaces of completed attempts, and
namespaces belonging to idle or deleted users. Preview its plan first:

```bash
LAB_MODE=kubernetes LAB_KUBECONFIG=$HOME/.kube/config \
  .venv/bin/python -m app.cleanup --idle-days 30
```

Run the deletion after reviewing the plan:

```bash
LAB_MODE=kubernetes LAB_KUBECONFIG=$HOME/.kube/config \
  .venv/bin/python -m app.cleanup --idle-days 30 --apply
```

| Flag | Meaning |
| --- | --- |
| `--idle-days N` | Inactivity threshold; defaults to `LAB_SANDBOX_IDLE_DAYS` |
| `--all` | Select all users, including active users |
| `--keep-orphans` | Keep namespaces whose users no longer exist |
| `--apply` | Delete selected namespaces instead of showing a dry run |

Deletion is asynchronous. A namespace can remain `Terminating` while
Kubernetes removes its resources. The Helm deployment runs cleanup through a
CronJob; standalone runs can use `LAB_AUTO_CLEANUP=true`.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `forbidden: cannot impersonate resource` | Confirm the provisioner ClusterRoleBinding and the `lab-runner` impersonation rule. |
| `forbidden` when creating a RoleBinding | Confirm the provisioner can `bind` the `lab-user` ClusterRole. |
| API discovery fails only for commands using `--as` | Provide an explicit kubeconfig, as the Helm chart does. |
| `ImagePullBackOff` appears in every challenge | Confirm `LAB_APP_IMAGE` exists and cluster nodes can reach its registry. |
| A namespace remains `Terminating` | Wait for deletion to finish before retrying that challenge. |
| `kubectl` binary is missing | Add `kubectl` to the platform image. |
| A Pod is rejected by Pod Security Admission | Check cluster policy and the lab namespace's `baseline` label. |
