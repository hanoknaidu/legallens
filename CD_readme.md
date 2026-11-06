# Deploying Legal Lens to AKS — resource plan

A checklist of every Azure resource needed to get this product running in an AKS
cluster, with both the `az` command and the portal click-path for each one.

**This is not a production setup**, and it does not try to be. One node, one replica,
a public IP with no TLS, and no autoscaling. It is the shortest honest path from "it
runs in Docker on my laptop" to "it runs in Kubernetes and I can send someone the
link." Where a shortcut is taken, it is marked **Shortcut** and says what the real
answer would be.

There are no CD pipelines here. [Part 3](#part-3--what-cd-will-need-later) lists what
a pipeline would need from the resources below, so nothing has to be rebuilt when
that work starts.

---

## Part 0 — What we are actually deploying

Two containers. Knowing their shape is what makes the resource list make sense.

| | Backend | Frontend |
|---|---|---|
| What it is | FastAPI + uvicorn | static bundle served by nginx |
| Image size | 195 MB (amd64) | 61 MB |
| Listens on | 8000 | 8080 |
| Health probe | `GET /api/health` | `GET /healthz` |
| Runs as | uid 10001 | uid `nginx`, non-root |
| Needs a disk? | **Yes** — SQLite, uploaded PDFs, vector index, all under `/data` | No |
| Needs a secret? | `ANTHROPIC_API_KEY`, unless run with the `echo` provider | No |
| Public? | No — only the frontend talks to it | **Yes** |

The browser only ever talks to the frontend. nginx inside it proxies `/api` to the
backend over the cluster network, which is why the app is same-origin and CORS never
comes up. Exactly the arrangement `docker-compose.yml` already makes locally.

Three properties of this app drive real decisions later, so they are worth stating up
front:

1. **The backend is single-writer.** SQLite takes one writer, the pending-turn
   registry lives in memory, and `WORKERS` must stay 1 (see `backend/Dockerfile`).
   So: **one replica**, and `Recreate` rather than `RollingUpdate`.
2. **Its data is on disk, not in a database service.** No volume means every uploaded
   document disappears when the pod restarts.
3. **AKS nodes are x86.** If you build on an Apple Silicon Mac, you must pass
   `--platform linux/amd64` or the pod will crash-loop with an exec format error.

---

## Part 1 — Prerequisites

- [ ] **An Azure account.** A new one comes with free credit; a student account works.
      Sign up at <https://azure.microsoft.com/free>.
- [ ] **Azure CLI** — `brew install azure-cli` (macOS) or
      <https://learn.microsoft.com/cli/azure/install-azure-cli>. Check with `az version`.
- [ ] **kubectl** — `az aks install-cli` installs it, or `brew install kubectl`.
- [ ] **Docker Desktop**, running. You already have this; it is what builds the images.
- [ ] **Sign in** — `az login`. A browser window opens. Then pick a subscription if
      you have more than one:

```bash
az account list --output table
az account set --subscription "<subscription name or id>"
az account show --output table          # confirm you are where you think you are
```

> **Portal equivalent:** <https://portal.azure.com> — the subscription picker is in
> the top-right account menu. Everything below can be done in the portal, but the
> CLI is what a pipeline will eventually run, so prefer it where you can.

---

## Part 2 — The resources, in order

Set these once and reuse them in every command. **`ACR_NAME` must be globally unique
across all of Azure and lowercase alphanumeric only** — no hyphens. Put your initials
or a number in it.

```bash
export RG=legallens-rg
export LOCATION=eastus
export ACR_NAME=legallensacr001        # <-- change this, must be globally unique
export AKS_NAME=legallens-aks
```

| # | Resource | Why it is needed | Roughly what it costs |
|---|---|---|---|
| 1 | Resource group | A folder for everything else. Deleting it deletes the lot — which is how you avoid a surprise bill. | Free |
| 2 | Container registry (ACR) | AKS pulls images from a registry; it cannot use your laptop's Docker. | Basic ≈ $5/month |
| 3 | AKS cluster | Runs the containers. | Control plane free on the Free tier; **you pay for the node VM** |
| 4 | ACR ↔ AKS role assignment | Lets the cluster pull from your registry without a password. | Free |
| 5 | Kubernetes Secret | Holds `ANTHROPIC_API_KEY`. | Free (in-cluster) |
| 6 | PersistentVolumeClaim → Azure Disk | The backend's `/data`. Provisioned automatically by the PVC. | Small managed disk, a few $/month |
| 7 | Deployments + Services | The workloads, and a public IP for the frontend. | The public IP has a small hourly charge |

Prices move and vary by region — treat these as orders of magnitude and check
<https://azure.microsoft.com/pricing/calculator/>. **A cluster left running over a
weekend costs real money.** See [Part 4](#part-4--tearing-it-all-down).

---

### Step 1 — Resource group

Everything else goes inside it, and deleting it is the cleanup story.

**CLI**

```bash
az group create --name $RG --location $LOCATION
```

**Portal**

1. Search **Resource groups** → **+ Create**.
2. Subscription: yours. Name: `legallens-rg`. Region: `East US`.
3. **Review + create** → **Create**.

---

### Step 2 — Azure Container Registry (ACR)

**CLI**

```bash
az acr create --resource-group $RG --name $ACR_NAME --sku Basic
az acr show --name $ACR_NAME --query loginServer --output tsv    # <name>.azurecr.io
```

**Portal**

1. Search **Container registries** → **+ Create**.
2. Resource group `legallens-rg`, registry name (globally unique), region, SKU
   **Basic**.
3. **Review + create** → **Create**.

> **Shortcut:** Basic SKU, no geo-replication, no private endpoint. Fine for a class;
> a real deployment would at least use Standard and lock down public access.

---

### Step 3 — AKS cluster

The one that takes a few minutes. `--node-count 1` and a small VM keep it cheap.

**CLI**

```bash
az aks create \
  --resource-group $RG \
  --name $AKS_NAME \
  --node-count 1 \
  --node-vm-size Standard_B2s \
  --tier free \
  --generate-ssh-keys \
  --attach-acr $ACR_NAME
```

`--attach-acr` does Step 4 at the same time. If it fails with a permissions error —
it needs rights to create a role assignment — create the cluster without it and do
Step 4 separately, or ask whoever owns the subscription to run it.

**Portal**

1. Search **Kubernetes services** → **+ Create** → **Create a Kubernetes cluster**.
2. Basics: resource group, cluster name, region, **Free** pricing tier.
3. Node pools: edit the default pool → node count **1**, size **B2s**.
4. Integrations tab: pick your registry under **Container registry**.
5. **Review + create** → **Create**. It takes 5–10 minutes.

> **Shortcut:** one node, no availability zones, no autoscaling, public API server.
> A real cluster would have at least three nodes across zones and a private API
> server.

---

### Step 4 — Let AKS pull from ACR

Skip if `--attach-acr` already worked above. This creates a role assignment giving
the cluster's identity `AcrPull` on the registry — no passwords anywhere.

**CLI**

```bash
az aks update --resource-group $RG --name $AKS_NAME --attach-acr $ACR_NAME
```

**Portal**

Kubernetes service → **Settings → Container registry** → select the registry → Save.

---

### Step 5 — Point kubectl at the cluster

**CLI**

```bash
az aks get-credentials --resource-group $RG --name $AKS_NAME
kubectl get nodes          # expect one node, STATUS Ready
```

**Portal:** no equivalent — the portal has a browser shell (**Connect** button) but
you still run these same commands in it.

---

### Step 6 — Build and push the two images

From the repository root. **The `--platform` flag is not optional on a Mac.**

```bash
ACR_LOGIN=$(az acr show --name $ACR_NAME --query loginServer --output tsv)
az acr login --name $ACR_NAME

docker build --platform linux/amd64 -t $ACR_LOGIN/legallens-backend:v1 ./backend
docker build --platform linux/amd64 -t $ACR_LOGIN/legallens-frontend:v1 ./frontend

docker push $ACR_LOGIN/legallens-backend:v1
docker push $ACR_LOGIN/legallens-frontend:v1

az acr repository list --name $ACR_NAME --output table    # confirm both are there
```

**Portal:** you cannot push an image from the portal. You can browse what you pushed
under the registry's **Repositories** blade.

> **Tag with `v1`, not `latest`.** `latest` makes it impossible to tell what is
> running, and Kubernetes will not re-pull it reliably. This is the habit a CD
> pipeline will build on — it will tag with the git SHA.

> **Alternative:** `az acr build --registry $ACR_NAME --image legallens-backend:v1 ./backend`
> builds *in Azure* instead of locally. Slower to start, but it sidesteps the
> architecture problem entirely and needs no local Docker.

---

### Step 7 — The API key, as a Kubernetes Secret

**Shortcut for a first deploy:** skip this entirely. The backend runs with
`LEGAL_LENS_LLM_PROVIDER=echo`, a deterministic stub that makes no vendor call and
needs no key. The app comes up, documents upload, and answers are canned. Do this
first — it proves the cluster works without spending anything on tokens.

For real answers:

```bash
kubectl create secret generic legallens-secrets \
  --from-literal=ANTHROPIC_API_KEY='sk-ant-...'
```

**Portal:** Kubernetes service → **Kubernetes resources → Secrets → + Create**. Note
the portal shows secret values base64-encoded, which is encoding, not encryption.

> **Shortcut:** a plain Kubernetes Secret is base64 in etcd, not a vault. The real
> answer is Azure Key Vault with the Secrets Store CSI driver, which is one more
> resource and one more concept — deliberately out of scope here.

---

### Step 8 — Storage for the backend

The PVC is the resource; Azure provisions the actual disk when the pod starts.

`ReadWriteOnce` is correct here, not a limitation to work around: one disk, one pod,
which is exactly what a single-writer SQLite app wants.

```yaml
# k8s/pvc.yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: legallens-data
spec:
  accessModes: ["ReadWriteOnce"]
  storageClassName: managed-csi       # default Azure Disk class
  resources:
    requests:
      storage: 8Gi
```

```bash
kubectl apply -f k8s/pvc.yaml
kubectl get pvc          # Pending until a pod mounts it — that is normal
```

---

### Step 9 — Deploy the backend

```yaml
# k8s/backend.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: backend
spec:
  replicas: 1                 # must stay 1: SQLite, in-memory registry, WORKERS=1
  strategy:
    type: Recreate            # RollingUpdate would deadlock: the new pod cannot
                              # attach a disk the old pod still holds
  selector:
    matchLabels: { app: backend }
  template:
    metadata:
      labels: { app: backend }
    spec:
      securityContext:
        fsGroup: 10001        # the image runs as uid 10001; without this the
                              # mounted disk is root-owned and /data is read-only
      containers:
        - name: backend
          image: <ACR_LOGIN>/legallens-backend:v1     # substitute your login server
          ports:
            - containerPort: 8000
          env:
            - name: LEGAL_LENS_LLM_PROVIDER
              value: "echo"                 # change to "anthropic" once the Secret exists
            - name: LEGAL_LENS_ENVIRONMENT
              value: "prod"
          # Uncomment with the Secret from Step 7:
          # envFrom:
          #   - secretRef: { name: legallens-secrets }
          volumeMounts:
            - name: data
              mountPath: /data
          readinessProbe:
            httpGet: { path: /api/health, port: 8000 }
            initialDelaySeconds: 10
          livenessProbe:
            httpGet: { path: /api/health, port: 8000 }
            initialDelaySeconds: 40         # matches the image's start-period
      volumes:
        - name: data
          persistentVolumeClaim:
            claimName: legallens-data
---
apiVersion: v1
kind: Service
metadata:
  name: backend               # the name matters — see the note below
spec:
  type: ClusterIP             # internal only; the frontend reaches it, the internet does not
  selector: { app: backend }
  ports:
    - port: 8000
      targetPort: 8000
```

> **Why the Service must be called `backend`:** the frontend image defaults to
> `BACKEND_URL=http://backend:8000`. Name the Service `backend` and listen on 8000,
> and the frontend needs no configuration at all. Name it anything else and you must
> set `BACKEND_URL` to match.

---

### Step 10 — Deploy the frontend, and get a public IP

```yaml
# k8s/frontend.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: frontend
spec:
  replicas: 1                 # stateless; this one could safely be scaled up
  selector:
    matchLabels: { app: frontend }
  template:
    metadata:
      labels: { app: frontend }
    spec:
      containers:
        - name: frontend
          image: <ACR_LOGIN>/legallens-frontend:v1
          ports:
            - containerPort: 8080
          readinessProbe:
            httpGet: { path: /healthz, port: 8080 }
          livenessProbe:
            httpGet: { path: /healthz, port: 8080 }
---
apiVersion: v1
kind: Service
metadata:
  name: frontend
spec:
  type: LoadBalancer          # this is what creates the public IP
  selector: { app: frontend }
  ports:
    - port: 80
      targetPort: 8080
```

```bash
kubectl apply -f k8s/backend.yaml
kubectl apply -f k8s/frontend.yaml

kubectl get pods -w                      # wait for both to be Running and Ready
kubectl get service frontend -w          # EXTERNAL-IP is <pending> for a minute or two
```

Then open `http://<EXTERNAL-IP>` in a browser.

The frontend starts whether or not the backend is up — nginx resolves the upstream
per request — so a frontend that loads with API errors means the backend pod, not the
frontend.

> **Shortcut:** `LoadBalancer` + a bare IP + plain HTTP. No DNS name, no certificate.
> The real answer is an Ingress controller with cert-manager and a hostname, which is
> a whole additional topic. Do not put real documents through this.

---

### Troubleshooting, in the order things usually break

| Symptom | Almost always |
|---|---|
| `ImagePullBackOff` | Step 4 was skipped, or the image tag is wrong. `kubectl describe pod <name>` says which. |
| `CrashLoopBackOff`, logs say `exec format error` | Built for arm64. Rebuild with `--platform linux/amd64`. |
| Pod `Running` but never `Ready` | Readiness probe failing. `kubectl logs <pod>`; try `kubectl exec <pod> -- wget -qO- localhost:8000/api/health`. |
| Backend logs a read-only or permission error on `/data` | `fsGroup: 10001` missing from the pod spec. |
| `EXTERNAL-IP` stuck on `<pending>` for 5+ minutes | Quota or region issue. `kubectl describe service frontend`. |
| PVC stuck `Pending` | Normal until a pod mounts it. Still pending after the pod starts → `kubectl describe pvc legallens-data`. |

Useful always:

```bash
kubectl get pods
kubectl logs deploy/backend --tail=50
kubectl describe pod <pod-name>
kubectl exec -it deploy/backend -- sh
```

---

## Part 3 — What CD will need later

Not built yet. Listed so the resources above do not have to be revisited.

- [ ] **A way for GitHub Actions to authenticate to Azure.** The modern answer is
      **workload identity federation** — an app registration with a federated
      credential trusting your GitHub repo, so there is no secret to rotate. The
      older answer is a service principal with a client secret in
      `AZURE_CREDENTIALS`. Prefer federation.
- [ ] **Role assignments for that identity:** `AcrPush` on the registry, and
      `Azure Kubernetes Service Cluster User Role` on the cluster.
- [ ] **A GitHub environment** (e.g. `production`) if you want a manual approval gate
      before deploys.
- [ ] **Image tags from the git SHA**, not `latest`, so a deployment can be traced to
      a commit and rolled back to one.
- [ ] **The manifests in the repo** — the YAML above belongs in a `k8s/` directory,
      with the image tag templated, so the pipeline applies version control rather
      than something pasted into a terminal once.
- [ ] **A trigger decision:** deploy on every merge to `main`, or only on a tag.

The CI side is already done and the hook is in place: `.github/workflows/ci.yml`
builds and boots the image on every PR, and its `push: [main]` trigger is the signal
a deploy workflow reads. See `backend/CI.md`.

---

## Part 4 — Tearing it all down

**Do this at the end of every session.** The cluster bills by the hour whether or not
anyone is using it, and this is the single most common way a class costs someone real
money.

```bash
az group delete --name $RG --yes --no-wait
```

One command, because every resource lives in that group — cluster, registry, disks,
public IP. Verify with `az group list --output table`.

**Portal:** Resource groups → `legallens-rg` → **Delete resource group** → type the
name to confirm.

To pause instead of delete — keeps the disk and the images, stops the VM charge:

```bash
az aks stop  --resource-group $RG --name $AKS_NAME
az aks start --resource-group $RG --name $AKS_NAME
```

---

## Decisions still open

Worth settling before anyone follows this end to end:

1. **Region.** `eastus` above. Pick whichever is nearest or wherever the student
   subscriptions have quota — B-series quota in a busy region is a common blocker.
2. **One resource group per student, or one shared?** Per student is cleaner to clean
   up and to reason about; shared is cheaper but means unique names for everything.
3. **Does the class deploy with the `echo` provider or a real key?** Echo costs
   nothing and needs no secret, which makes Step 7 optional. A real key means
   per-student keys or one shared key, and real spend.
4. **Does this ever need to survive a pod restart with real documents?** If yes, the
   honest answer is Azure Database for PostgreSQL plus Blob Storage rather than
   SQLite on a disk — a bigger change to the app, not just to the deployment.
5. **TLS and a hostname?** Needed the moment anyone outside the class opens it.
   Ingress + cert-manager, and its own session.
