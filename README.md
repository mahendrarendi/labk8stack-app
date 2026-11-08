# LabK8stack

LabK8stack is a browser-based Kubernetes troubleshooting lab. Learners inspect a
broken workload, edit its manifest or use a restricted `kubectl` terminal, apply
their fix, and verify the live sandbox. The platform provides ten challenges,
timed attempts, scores, and a rolling seven-day leaderboard.

The challenge page shows symptoms and goals without revealing the faulty field
or the solution. An explanation becomes available after an attempt is passed or
expires.

## How a challenge works

1. Create an account or sign in and select a challenge.
2. The server starts a timer and prepares a sandbox with a broken workload.
3. Inspect the workload, edit the manifest or run a supported terminal command,
   and apply the change.
4. Select **Verify**. The platform checks the applied state, not an unsaved
   editor draft.
5. Passing the challenge awards points based on time remaining and attempts.

**Reset Lab** clears challenge resources and requires a new apply before
verification. The editor and terminal operate on the same sandbox. Navigation
preserves the editor draft during the browser session.

## Main features

- Username and password accounts with signed session cookies.
- Ten challenges at Easy, Medium, and Hard levels.
- Manifest editor with apply output and a restricted terminal with command
  history, `help`, `clear`, and Ctrl+L.
- Per-attempt timer, scoring, and a rolling seven-day leaderboard.
- PostgreSQL storage for accounts, attempts, and scores.
- `/healthz`, `/readyz`, and `/metrics` endpoints.
- `fake` mode for local simulation and `kubernetes` mode for real namespaces.
- Cleanup of expired, idle, completed, and orphaned sandboxes.

## Run locally

Create a `.env` file from the example and set the required credentials:

```bash
cp .env.example .env
docker compose up -d --build
docker compose ps
```

Open `http://localhost` (or the port set by `NGINX_PORT`). Compose starts
PostgreSQL 17, the platform, and Nginx. PostgreSQL data is kept in the
`db-data` volume. `docker compose down` retains that data; `docker compose
down -v` removes it.

To run without Compose, provide a reachable PostgreSQL URL and use the Makefile:

```bash
make setup
export DATABASE_URL=postgresql://k8slab:password@localhost:5432/k8slab
make run
```

`make run` and `make serve` run database migrations before starting the server.
Use `make help` for the command list. Tests that access the database require a
separate PostgreSQL database:

```bash
export TEST_DATABASE_URL=postgresql://k8slab:password@localhost:5432/k8slab_test
make test
```

## Run against a Kubernetes cluster

Set `LAB_MODE=kubernetes` and supply a kubeconfig when running outside the
cluster. The legacy standalone manifests and RBAC setup are described in
[the cluster guide](docs/real-cluster.md). The current Helm chart and GitOps
configuration are maintained in the sibling `../k8s-lab-deploy/` repository.

```bash
kubectl apply -f deploy/rbac.yaml
LAB_MODE=kubernetes LAB_KUBECONFIG=$HOME/.kube/config \
  .venv/bin/uvicorn app.main:app --port 8080
```

Each user/challenge pair gets a namespace such as
`lab-u<id>-image-pull-backoff`. The platform creates a `lab-runner`
ServiceAccount and binds it to the `lab-user` ClusterRole in that namespace.
Learner commands run through `kubectl --as` with that identity. The deployed
chart mounts an explicit in-cluster kubeconfig so impersonated commands retain
the API server address and use the Pod's rotating ServiceAccount token.

A sandbox namespace is deleted after a challenge passes or expires. Retries
within an active attempt reuse the namespace after cleaning challenge
resources. The cleanup process removes namespaces left behind after their
challenge duration and `LAB_SANDBOX_GRACE_MINUTES` have elapsed. It also handles
idle and orphaned users. Preview or run cleanup manually with:

```bash
LAB_MODE=kubernetes LAB_KUBECONFIG=$HOME/.kube/config \
  .venv/bin/python -m app.cleanup --idle-days 30
LAB_MODE=kubernetes LAB_KUBECONFIG=$HOME/.kube/config \
  .venv/bin/python -m app.cleanup --idle-days 30 --apply
```

The Helm deployment uses a CronJob for cleanup. Outside the cluster, the
platform can run cleanup periodically when `LAB_AUTO_CLEANUP=true`.

## Accounts and scoring

**Create account** requires a username and a password of at least six
characters. **Sign in** restores the user's account, attempts, and score.
Usernames are case insensitive. Passwords are stored with PBKDF2-HMAC-SHA256,
600,000 iterations, and a random salt per user. Existing accounts from the old
name-only flow can be claimed once through **Create account** with the same
username.

A passing attempt receives between 50% and 100% of the challenge points based
on time remaining, minus ten points for each additional attempt, with a
minimum of 20 points. The leaderboard sums passed attempts from the last seven
days and breaks ties by earlier completion time.

## Challenges

| Slug | Visible symptom | Fault to diagnose | Points |
| --- | --- | --- | ---: |
| `image-pull-backoff` | Deployment never becomes ready | Image tag does not exist | 100 |
| `service-no-endpoints` | Service returns 503 | Service selector does not match Pod labels | 110 |
| `pending-resources` | Pod never schedules | Resource requests exceed node capacity | 140 |
| `command-override` | Container restarts in a loop | Command overrides the image entrypoint | 110 |
| `oom-killed` | Container is killed while starting | Memory limit is too low | 140 |
| `readiness-probe` | Pods never become Ready | Probe uses the wrong path | 130 |
| `port-env` | Application is unreachable after a config change | `PORT` differs from the probe port | 130 |
| `service-target-port` | Service connections are refused | Service `targetPort` is incorrect | 130 |
| `deployment-selector` | Deployment cannot be applied | Deployment selector differs from template labels | 150 |
| `deployment-paused` | Rollout never progresses | Deployment rollout is paused | 150 |

The fault column is for maintainers. Keep faults and replacement values out of
challenge summaries, stories, objectives, and hints. To add a challenge, define
its broken manifest, validator, simulated behavior, status, events, and logs in
`app/challenges.py`.

In Kubernetes mode, verification requires both a valid applied manifest and a
healthy live Deployment or Service endpoint. In fake mode, the engine simulates
the same workflow.

## Terminal commands

The fake-mode terminal supports the following commands. They modify the same
sandbox as the manifest editor:

```text
kubectl get pods|deploy|svc|ep
kubectl describe pod|deployment|service <name>
kubectl logs <pod>
kubectl set image deployment/web <container>=<image>
kubectl set env deployment/web <KEY>=<value>
kubectl set resources deployment/web --limits=cpu=500m,memory=256Mi
kubectl scale deployment/web --replicas=<n>
kubectl rollout status|resume|pause deployment/web
kubectl delete pod <name>
kubectl create deployment <name> --image=<image>
help
clear
```

In Kubernetes mode, supported commands run through real `kubectl` in the
learner's namespace. Namespace and identity override flags, shell operators,
and commands outside the allowlist are rejected.

## Security and operations

- Set a strong `SECRET_KEY` and use HTTPS with `COOKIE_SECURE=true` before
  exposing the site.
- The platform can create namespaces and impersonate the lab runner. Use a
  dedicated training cluster for untrusted public labs.
- The legacy standalone manifests live in `deploy/`; the maintained Helm chart
  and Argo CD Applications live in `../k8s-lab-deploy/`.
- The project currently has no email verification, password reset, or login
  rate limiting. Add these controls before broad public access.
- Use a dedicated PostgreSQL database for tests and keep production backups
  outside the cluster. See the deployment repository for CNPG guidance.

## Project layout

```text
app/                 FastAPI application, engines, templates, and static assets
tests/               API, engine, and lifecycle tests
docs/real-cluster.md Legacy standalone cluster setup and troubleshooting
deploy/              Legacy standalone Kubernetes manifests
.env.example         Local configuration reference
Dockerfile           Platform container image
Makefile             Local development commands
```
