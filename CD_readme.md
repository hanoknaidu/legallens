# Deploying Legal Lens to AKS — resource plan

A checklist of every Azure resource needed to get this product running in an AKS
cluster, with both the `az` command and the portal click-path for each one.

**This is not a production setup**, and it does not try to be. One node, one replica,
a public IP with no TLS, and no autoscaling. It is the shortest honest path from "it
runs in Docker on my laptop" to "it runs in Kubernetes and I can send someone the
link." Where a shortcut is taken, it is marked **Shortcut** and says what the real
answer would be.

The pipeline exists: `.github/workflows/cd.yml` builds, pushes and rolls out on CI
succeeding on `main`. [Part 3](#part-3--setting-up-cd) is what it needs from the
resources below — the federated identity, the role assignments and the repository
variables — and [where this project stands today](#where-this-project-stands-today)
records which of those are actually in place.

Parts 1 and 2 are still worth walking through first: they build the cluster the
pipeline deploys into, and doing one deploy by hand is how you find out whether a
failure is the pipeline's or the cluster's.

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
export RG=rg-legalens
export LOCATION=eastus
export ACR_NAME=legallensacr1          # globally unique; already taken by this project
export AKS_NAME=aks-legallens
```

Those are the names **this project actually uses** — the resources already exist, so
the values above are what the later steps and the pipeline's repository variables are
set to. Two places where the live setup differs from the walkthrough below, neither
of which matters to the deploy: the registry is **Standard** rather than Basic, and
the node pool runs **2 nodes** rather than 1.

| # | Resource | Why it is needed | Roughly what it costs |
|---|---|---|---|
| 1 | Resource group | A folder for everything else. Deleting it deletes the lot — which is how you avoid a surprise bill. | Free |
| 2 | Container registry (ACR) | AKS pulls images from a registry; it cannot use your laptop's Docker. | Basic ≈ $5/month; **Standard** (what is deployed) ≈ $20/month |
| 3 | AKS cluster | Runs the containers. | Control plane free on the Free tier; **you pay for the node VMs** — `Standard_D2s_v4` ≈ $0.096/hour each, and the pool runs 2 |
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

The one that takes a few minutes. `--node-count 1` keeps it to a single node.
`Standard_D2s_v4` — the Dsv4-series 2-vCPU size, 2 vCPU and 8GB — is comfortable for
the default stack and roughly $0.096/hour, about $70/month if you leave it running,
which is why [Part 4](#part-4--tearing-it-all-down) matters. It is **tight** for the
full retrieval stack: the 3Gi request fits, the 8Gi limit does not — see
[below](#switching-on-the-full-retrieval-stack).

`--kubernetes-version` pins the control plane. Omit it and you get whatever the
region's default is that week, which makes "it worked last month" hard to reason
about. Check what your region actually offers before you copy the value below:

```bash
az aks get-versions --location $LOCATION --output table
```

**CLI**

```bash
az aks create \
  --resource-group $RG \
  --name $AKS_NAME \
  --node-count 1 \
  --node-vm-size Standard_D2s_v4 \
  --kubernetes-version 1.36.5 \
  --tier free \
  --generate-ssh-keys \
  --attach-acr $ACR_NAME
```

`--attach-acr` does Step 4 at the same time. If it fails with a permissions error —
it needs rights to create a role assignment — create the cluster without it and do
Step 4 separately, or ask whoever owns the subscription to run it.

**Portal**

1. Search **Kubernetes services** → **+ Create** → **Create a Kubernetes cluster**.
2. Basics: resource group, cluster name, region, **Free** pricing tier, and
   **Kubernetes version** `1.36.5`.
3. Node pools: one pool is all you need. Edit the default pool → node count **1**,
   size **D2s v4** (`Standard_D2s_v4`, under the **Dsv4-series** family). Leave
   **node auto-provisioning** disabled — nothing here needs the cluster to invent
   node pools, and a disabled autoscaler is one less thing that can surprise the
   bill.
4. Integrations tab: pick your registry under **Container registry**.
5. **Review + create** → **Create**. It takes 5–10 minutes.

Confirm what you got:

```bash
kubectl version                             # server should report v1.36.5
kubectl get nodes -o wide                   # one node
kubectl get node -o jsonpath='{.items[0].metadata.labels.node\.kubernetes\.io/instance-type}{"\n"}'
```

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

# Make `legallens` the default namespace for this context, so none of the kubectl
# commands in this guide needs `-n legallens`. Skip it and every one of them looks
# in `default` and reports nothing found.
kubectl config set-context --current --namespace=legallens
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

### Steps 8–10 — The workloads

The manifests live in **`k8s/`** and are the same ones CD applies. They are not
reproduced here on purpose: a copy in a README and a copy in the repository drift,
and then the one you read is not the one that runs.

| File | What it declares |
|---|---|
| `k8s/namespace.yaml` | the `legallens` namespace everything else lives in |
| `k8s/pvc.yaml` | an 8Gi Azure Disk for the backend's `/data` |
| `k8s/backend.yaml` | the API Deployment, and a **ClusterIP** Service — internal only |
| `k8s/frontend.yaml` | the UI Deployment, and a **LoadBalancer** Service — the public IP |

Four details in them are decisions rather than boilerplate, and each is commented in
place:

- **`replicas: 1` and `strategy: Recreate`** on the backend. It is single-writer:
  SQLite takes one writer, the pending-turn registry is in memory, and the image
  pins `WORKERS=1`. `RollingUpdate` would deadlock regardless — the new pod cannot
  attach a `ReadWriteOnce` disk the old pod still holds.
- **`fsGroup: 10001`.** The image runs as that uid. Without it the mounted disk is
  root-owned, `/data` is not writable, and the error you see is a confusing runtime
  failure rather than a mount failure.
- **The backend Service is named `backend` on port 8000.** That is already the
  frontend image's default `BACKEND_URL`, so the frontend needs no configuration.
  Rename the Service and you must set `BACKEND_URL` to match.
- **Probes point at `/api/health` and `/healthz`.** The frontend's is answered by
  nginx alone — a readiness check that reached through to the API would take the UI
  out of service whenever the API was briefly unwell.

Every object in them names the **`legallens`** namespace, so `kubectl apply -f` puts
it in the right place with no `-n` flag, and `kubectl delete namespace legallens` is a
complete uninstall. Nothing this app owns is in `default`.

To apply them **by hand** (CD does this for you — see Part 3). All seven values in
them are placeholders, so substitute them the same way the pipeline does — including
in `pvc.yaml`, which carries the disk size:

```bash
export ACR_LOGIN=$(az acr show --name $ACR_NAME --query loginServer --output tsv)
export BACKEND_IMAGE=$ACR_LOGIN/legallens-backend:v1
export FRONTEND_IMAGE=$ACR_LOGIN/legallens-frontend:v1
export LLM_PROVIDER=echo        # or "anthropic" once the Secret from Step 7 exists

# The four the pipeline derives from BACKEND_EXTRAS. These are the default stack;
# for the full retrieval stack use 32Gi / 3Gi / 8Gi / 500m.
export DATA_VOLUME_SIZE=8Gi
export BACKEND_MEMORY_REQUEST=512Mi
export BACKEND_MEMORY_LIMIT=2Gi
export BACKEND_CPU_REQUEST=250m

names='${BACKEND_IMAGE} ${FRONTEND_IMAGE} ${LLM_PROVIDER} ${DATA_VOLUME_SIZE}'
names="${names} "'${BACKEND_MEMORY_REQUEST} ${BACKEND_MEMORY_LIMIT} ${BACKEND_CPU_REQUEST}'

kubectl apply -f k8s/namespace.yaml      # first: the rest live inside it
for m in k8s/pvc.yaml k8s/backend.yaml k8s/frontend.yaml; do
  envsubst "$names" < "$m" | kubectl apply -f -
done

kubectl get pods -n legallens -w               # both Running and Ready
kubectl get service frontend -n legallens -w   # EXTERNAL-IP is <pending> for a minute or two
```

`envsubst` with an explicit name list, not bare `envsubst`: it restricts substitution
to exactly these seven, so a `$` anywhere else in a manifest survives untouched.

Then open `http://<EXTERNAL-IP>`.

The frontend starts whether or not the backend is up — nginx resolves the upstream
per request — so a UI that loads with API errors points at the backend pod, not the
frontend.

> **Shortcut:** `LoadBalancer` + a bare IP + plain HTTP. No DNS name, no certificate.
> The real answer is an Ingress controller with cert-manager and a hostname, which is
> a topic of its own. Do not put real documents through this.

### Troubleshooting, in the order things usually break

| Symptom | Almost always |
|---|---|
| `ImagePullBackOff` | Step 4 was skipped, or the image tag is wrong. `kubectl describe pod <name>` says which. |
| `CrashLoopBackOff`, logs say `exec format error` | Built for arm64. Rebuild with `--platform linux/amd64`. |
| Pod `Running` but never `Ready` | Readiness probe failing. `kubectl logs <pod>`; try `kubectl exec <pod> -- wget -qO- localhost:8000/api/health`. |
| Backend logs a read-only or permission error on `/data` | `fsGroup: 10001` missing from the pod spec. |
| `EXTERNAL-IP` stuck on `<pending>` for 5+ minutes | Quota or region issue. `kubectl describe service frontend`. |
| PVC stuck `Pending` | Normal until a pod mounts it. Still pending after the pod starts → `kubectl describe pvc legallens-data`. |

Useful always — these assume the default namespace was set in
[Step 5](#step-5--point-kubectl-at-the-cluster); otherwise add `-n legallens`:

```bash
kubectl get pods
kubectl logs deploy/backend --tail=50
kubectl describe pod <pod-name>
kubectl exec -it deploy/backend -- sh
```

A `kubectl` command that reports nothing at all, rather than an error, almost always
means it looked in `default`. `kubectl config view --minify --output
'jsonpath={..namespace}'` prints the namespace the context is actually using.

---

## Part 3 — Setting up CD

The pipeline exists: `.github/workflows/cd.yml`, with the manifests it applies in
`k8s/`. It is not wired to your Azure yet, and it cannot be from inside the repo —
the four steps below are the ones only you can do.

### What it does

```
CI passes on main  ->  build-and-push            ->  deploy
                       two images -> ACR,            kubectl apply -> AKS,
                       tagged <git sha>              wait, smoke-test, roll back on failure
```

Two workflows. `ci.yml` is the gate; `cd.yml` ships what gets past it and onto
`main`. Merging is a person's job.

Three decisions worth knowing before you read the file:

**It triggers on CI succeeding, not on a push to `main`.** A `push` trigger runs CD
in parallel with the tests, so a commit can reach the cluster before anything has
verified it. `workflow_run` waits for CI's verdict and reads it. The cost is one
indirection; the benefit is that only green commits deploy.

**Every image is tagged with the git SHA. Nothing is tagged `latest`.** A moving tag
makes "what is running in the cluster?" unanswerable and makes rollback guesswork.
With SHA tags, `kubectl describe pod` names the commit it came from.

**The images are rebuilt in CD, not carried over from CI.** CI is deliberately
secretless — it holds no registry credential and cannot push — and passing a built
image between workflows means uploading and re-importing a tarball. A rebuild from
the same SHA is deterministic and takes about a minute with the layer cache warm.

### Where this project stands today

Verified against the live subscription and repository on 2026-11-06:

| | |
|---|---|
| Images tagged with the git SHA, never `latest` | **Yes** — `cd.yml` tags both images `:${{ needs.resolve.outputs.sha }}`, and `latest` appears nowhere in the workflows or the manifests |
| Deploy runs automatically once CI passes on `main` | **Yes** — `workflow_run` on CI, gated on `conclusion == 'success'`; CD has fired on its own after the last two green CI runs on `main` |
| App registration `legallens-cd` with both federated credentials | **Partly** — `github-main` (branch) and `github-env-production` (environment) both exist, but their subjects use the mutable `repo:hanoknaidu/legallens` prefix while this repository sends the immutable `repo:hanoknaidu@237133295/legallens@1440418111` form, so `azure/login` fails with `AADSTS700213`. Two matching credentials have to be added; see [Step 1](#step-1--an-identity-github-can-use-with-no-stored-password) |
| Repository variables and secrets | **Yes** — `ACR_NAME`, `AKS_NAME`, `AZURE_RESOURCE_GROUP`; `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` |
| `production` environment | **Yes**, unprotected — deploys need no approval |
| Role assignments on ACR and AKS (Step 2 below) | **No** — the identity has none, and nothing has been pushed to the registry yet |
| ACR ↔ AKS pull permission ([Step 4](#step-4--let-aks-pull-from-acr)) | **Yes** — the kubelet identity (`671a5de7…`) holds `AcrPull` scoped to `legallensacr1`, so the cluster can pull without a registry password |

So the pipeline is wired and the chain fires; what is missing is purely the three
role assignments. Until they exist, CD fails in `build-and-push` at `az acr login`.

### Step 1 — An identity GitHub can use, with no stored password

Workload identity federation: Azure trusts a token GitHub mints for your repository,
so there is no client secret to leak or rotate.

**CLI** — set `GH_ORG` and `GH_REPO` to your own:

```bash
export GH_ORG=hanoknaidu
export GH_REPO=legallens
export APP_NAME=legallens-cd

# The app registration and its service principal.
APP_ID=$(az ad app create --display-name $APP_NAME --query appId --output tsv)
az ad sp create --id $APP_ID
echo "AZURE_CLIENT_ID = $APP_ID"
```

Now **two** federated credentials. Two, not one, and this is the step people get
wrong: GitHub's token carries a different `subject` depending on the job. The build
job runs against the branch; the deploy job declares `environment: production`, which
changes the subject. One credential authenticates one of them and the other fails
with a confusing `AADSTS700213`.

The subject also has a prefix, and it is **not** reliably `repo:<org>/<repo>`. A
repository with GitHub's *immutable subject claim* enabled sends the numeric owner
and repository IDs inside it:

```
repo:myorg@237133295/myrepo@1440418111:ref:refs/heads/main
```

That exists so that deleting a repository and recreating one with the same name
cannot inherit its cloud access — the name is reusable, the IDs are not. It can be
on by default for new repositories, and a credential written the short way then
never matches anything. So ask GitHub what it will actually send rather than
assuming:

```bash
# Prints the exact prefix this repository's tokens will carry, immutable or not.
SUB_PREFIX=$(gh api "repos/${GH_ORG}/${GH_REPO}/actions/oidc/customization/sub" \
  --jq ".sub_claim_prefix // \"repo:${GH_ORG}/${GH_REPO}\"")
echo "subject prefix = ${SUB_PREFIX}"
```

```bash
az ad app federated-credential create --id $APP_ID --parameters "{
  \"name\": \"github-main\",
  \"issuer\": \"https://token.actions.githubusercontent.com\",
  \"subject\": \"${SUB_PREFIX}:ref:refs/heads/main\",
  \"audiences\": [\"api://AzureADTokenExchange\"]
}"

az ad app federated-credential create --id $APP_ID --parameters "{
  \"name\": \"github-env-production\",
  \"issuer\": \"https://token.actions.githubusercontent.com\",
  \"subject\": \"${SUB_PREFIX}:environment:production\",
  \"audiences\": [\"api://AzureADTokenExchange\"]
}"
```

If a deploy fails with `AADSTS700213`, read the subject Azure says it was presented
— `azure/login` prints it in the log under *Federated token details* — and compare it
character for character with `az ad app federated-credential list --id $APP_ID
--query '[].subject'`. The fix is always to make the credential match the token, not
the other way round.

**Portal**

1. **Microsoft Entra ID → App registrations → + New registration**. Name it
   `legallens-cd`, single tenant, no redirect URI. **Register**.
2. Copy the **Application (client) ID** and **Directory (tenant) ID** from Overview.
3. **Certificates & secrets → Federated credentials → + Add credential**.
4. Scenario: **GitHub Actions deploying Azure resources**. Fill in your org and
   repository. Entity type **Branch**, branch `main`. Name it `github-main`.
5. **Add a second credential** the same way, but entity type **Environment**, with
   environment name `production`.
6. Check the **Subject identifier** each one generated. The portal builds it from the
   org and repository *names*, so if this repository uses the immutable subject claim
   described above, both values are wrong and have to be edited to the `@<id>` form.
   The CLI block above avoids this by asking GitHub for the prefix.

> Do **not** create a client secret. The whole point of federation is that there is
> no secret to store.

### Step 2 — Give that identity permission

Two role assignments: push images, and reach the cluster.

```bash
SUB_ID=$(az account show --query id --output tsv)
ACR_ID=$(az acr show --name $ACR_NAME --query id --output tsv)
AKS_ID=$(az aks show --resource-group $RG --name $AKS_NAME --query id --output tsv)

az role assignment create --assignee $APP_ID --role AcrPush --scope $ACR_ID
az role assignment create --assignee $APP_ID \
  --role "Azure Kubernetes Service Cluster User Role" --scope $AKS_ID

echo "AZURE_SUBSCRIPTION_ID = $SUB_ID"
```

**Portal:** on the registry, and again on the cluster → **Access control (IAM) → +
Add role assignment** → pick the role → **Members → select the `legallens-cd` app**
→ Review + assign.

> If the cluster was created with **Azure RBAC for Kubernetes** enabled, Cluster User
> alone lets you fetch a kubeconfig but not apply anything. Add **Azure Kubernetes
> Service RBAC Writer** on the cluster too. A cluster created exactly as Part 2
> describes does not need it.

### Step 3 — Tell GitHub about your Azure

**Settings → Secrets and variables → Actions** in the repository.

**Variables** tab (not secret — they appear in logs and that is fine):

| Name | Value |
|---|---|
| `ACR_NAME` | your registry name, without `.azurecr.io` |
| `AKS_NAME` | your cluster name |
| `AZURE_RESOURCE_GROUP` | your resource group name |
| `BACKEND_EXTRAS` | **optional** — leave unset. `orchestrator workflows` switches on the real retrieval stack; see [below](#switching-on-the-full-retrieval-stack) |

**Secrets** tab:

| Name | Value |
|---|---|
| `AZURE_CLIENT_ID` | the app id from Step 1 |
| `AZURE_TENANT_ID` | `az account show --query tenantId -o tsv` |
| `AZURE_SUBSCRIPTION_ID` | `az account show --query id -o tsv` |
| `ANTHROPIC_API_KEY` | **optional** — see below |

**Leave `ANTHROPIC_API_KEY` out for the first deploy.** Without it the pipeline
deploys the backend with `LEGAL_LENS_LLM_PROVIDER=echo`, a deterministic stub that
makes no vendor call. The whole pipeline proves itself — build, push, rollout, smoke
test — before a single token is spent. Add the secret later and the next deploy
switches to the real provider on its own.

**CLI**, if you prefer:

```bash
gh variable set ACR_NAME --body "$ACR_NAME"
gh variable set AKS_NAME --body "$AKS_NAME"
gh variable set AZURE_RESOURCE_GROUP --body "$RG"

gh secret set AZURE_CLIENT_ID --body "$APP_ID"
gh secret set AZURE_TENANT_ID --body "$(az account show --query tenantId -o tsv)"
gh secret set AZURE_SUBSCRIPTION_ID --body "$(az account show --query id -o tsv)"
```

### Step 4 — The `production` environment

**Settings → Environments → New environment**, named `production`. Creating it is
enough; the deploy job references it, and an environment that does not exist is
created implicitly but will not show deployment history.

Leave it unprotected and deploys are automatic. If you later want a human to approve
each one, add **Required reviewers** here — no change to the workflow.

### Running it

Merge a pull request into `main`. CI runs on `main`; when it goes green, CD starts
on its own.

That chain works because a merge you perform is attributed to **you**. A merge
performed by a workflow would not be: GitHub does not raise events for pushes made
with the `GITHUB_TOKEN`, so CI would never run on `main` and CD would never see a
success to react to. Worth knowing if automatic merging is ever added — it has to
dispatch CD explicitly rather than rely on this trigger.

To deploy without a merge — after rebuilding the cluster, say — **Actions → CD → Run
workflow**.

### Switching on the full retrieval stack

The default deployment runs **real generation over weak retrieval**. The image is
built with no extras, so embeddings are the `hashing` stand-in and the vector store
is in-memory — not the `BAAI/bge-m3` stack the e2e run in
[docs/e2e-test-report.md](docs/e2e-test-report.md) measured. Answers come back
fluent; what suffers is *which pages get found*, which is the failure mode that
looks like the model being bad.

To run the real thing, set **one repository variable**:

| Variable | Value |
|---|---|
| `BACKEND_EXTRAS` | `orchestrator workflows` |

```bash
gh variable set BACKEND_EXTRAS --body "orchestrator workflows"
```

Then **Actions → CD → Run workflow**. Everything else follows from it, because
asking anyone to remember four coordinated changes is how a demo dies twenty minutes
in with an OOMKill:

| | Default | With the extras |
|---|---|---|
| Image size | 195 MB | **6.42 GB** |
| Provider | `anthropic` | `orchestrator` |
| Memory request / limit | 512Mi / 2Gi | **3Gi / 8Gi** |
| CPU request | 250m | 500m |
| Data volume | 8Gi | **32Gi** |

`HF_HOME` already points at `/data/hf` in both, so the ~4.5GB of model weights land
on the volume and download **once per volume** rather than once per pod. A pod
filesystem is ephemeral; without that, every restart re-downloads them.

**What to expect on the first run of it.** The build takes minutes rather than
seconds. The node then pulls 6.42GB before the container starts, which is why the
Deployment allows a 30-minute progress deadline and the pipeline waits 20 minutes
for the rollout. The first *question* asked of the app downloads the weights, so it
is slow in a way later ones are not.

**Check the node can take it — on `Standard_D2s_v4`, only just.** That size is 2 vCPU
and 8GB, of which AKS reserves roughly 1.5GB for the system, leaving about 5.5GB
allocatable. So:

- The **3Gi request schedules.** The pod will not sit `Pending`.
- The **8Gi limit cannot be honoured.** It is above the node's total RAM, so the
  ceiling that actually applies is the node running out of memory — the pod gets
  OOMKilled and restarts rather than being throttled at a limit. With ~2.5GB resident
  in model weights there is headroom for normal use, but a large document or two
  concurrent questions can cross it.
- The **500m CPU request** leaves half the box for everything else. CPU-only
  inference on 2 vCPU is slow; expect tens of seconds per question.

If you intend to demo the full stack rather than just prove it runs, resize the pool
first — `Standard_D4s_v5` (4 vCPU, 16GB) fits the 8Gi limit with room to spare:

```bash
az aks nodepool update --resource-group $RG --cluster-name $AKS_NAME \
  --name nodepool1 --node-vm-size Standard_D4s_v5
```

On a 2-vCPU/4GB node — a B-series, say — the 3Gi request does not even schedule: the
pod sits `Pending` with `Insufficient memory`.

**Going back** is the same variable, emptied:

```bash
gh variable set BACKEND_EXTRAS --body ""
```

The next deploy builds the small image again and asks for less memory. The disk does
**not** shrink — Azure disks expand but never contract — so you keep paying for 32Gi
until the resource group is deleted.

> **Is it worth it for a class?** Probably not. The default stack demonstrates the
> product end to end and deploys in a couple of minutes. The full stack is the right
> call when the thing being shown is *retrieval quality* specifically, and it is
> worth doing once so students see what the 6.42GB is actually made of — 4.1GB of it
> is CUDA code a CPU node will never execute (`backend/DOCKER.md`).

### Why not Helm

Because there are three manifests and one substituted value in each. A chart would
add a templating language, a values file, and a release lifecycle in order to express
`image: <this tag>` — concepts a reader of this repository would have to learn before
they could see what gets deployed. `envsubst` is two lines of shell and the output is
the YAML you already read.

The calculation changes the moment there is more than one environment to
differentiate — staging and production with different replica counts, hostnames and
resource limits. At that point Helm or Kustomize overlays earn their keep. One
cluster and one namespace do not.

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
   subscriptions have quota — quota in a busy region is a common blocker.
   `Standard_D2s_v4` needs 2 vCPU of `Standard DSv4 Family` quota; resizing to
   `Standard_D4s_v5` for the full retrieval stack needs 4 vCPU of `Standard DSv5
   Family`, which is a separate quota and not always granted on a fresh student
   subscription.
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
