# maf-demos

Progressive demos showing how to build an HR helper agent with the
[Microsoft Agent Framework](https://github.com/microsoft/agent-framework),
starting from a fully local model and ending with a Foundry-hosted agent
that combines an enterprise knowledge base with a Foundry Toolbox
(web search + code interpreter). 

**P.S. This is based on another Microsoft course - https://github.com/Azure-Samples/foundry-hosted-agentframework-demos/tree/main so please check the original one for more details**

The scripts share the same agent shape (instructions + tools) and only
swap out the chat client and tool list at each stage:

| # | Script | What it adds |
|---|---|---|
| 1 | `1-local-model.py` | Run an agent fully locally against an Ollama-served small model. No cloud. |
| 2 | `2-foundry-model.py` | Same agent, now using a chat model deployed in Microsoft Foundry / Azure OpenAI. |
| 3 | `3-foundry-iq.py` | Add a knowledge base as an MCP tool on the agent so answers are grounded in the indexed HR content. Requires the one-time `create-knowledge-base.py` setup first. |
| 4 | `4-toolbox.py` | Add a Foundry Toolbox that exposes web search and code interpreter via MCP, alongside the direct KB MCP tool. Requires the one-time `create-toolbox.py` setup first. |
| 5 | `5-hosted-agent.py` | Wrap the same agent in `ResponsesHostServer` so it can run as a Foundry Hosted Agent. Exposes the OpenAI Responses API locally for testing. |
| — | `create-knowledge-base.py` | One-time setup: upload sample HR docs to blob storage, build an Azure AI Search index with integrated vectorization, then create a Foundry IQ knowledge source + knowledge base on top. |
| — | `create-toolbox.py` | One-time setup: register the Foundry Toolbox (web search + code interpreter) in your Foundry project. |
| — | `test-hosted-agent-locally.py` | Sample client that POSTs a request to the local hosted-agent server and prints assistant text + tool calls. |

Sample HR content lives in [data/hr-kb/](data/hr-kb/).

## Prerequisites

- Python 3.13+
- [`uv`](https://docs.astral.sh/uv/)
- For script 1: [Ollama](https://ollama.com/download) running locally with a tool-calling model (e.g. `ollama pull llama3.2`)
- For scripts 2-5: an Azure subscription with
  - A Microsoft Foundry / Azure OpenAI resource and a chat deployment
  - An embedding deployment (e.g. `text-embedding-ada-002`)
  - An Azure AI Search service (Basic tier or higher, with a system-assigned managed identity)
  - A storage account + blob container
- [Azure Developer CLI](https://learn.microsoft.com/azure/developer/azure-developer-cli/install-azd) (`azd auth login`) on the machine that runs the scripts

### Required role assignments

There are **four identities** involved. Each needs different permissions on
different resources. None of these need keys — everything is RBAC + managed
identity.

| # | Identity | Where to find it |
|---|---|---|
| A | **You** (script runner) | `azd auth login` / `az login` user |
| B | **AI Search service MI** | Search service → Identity → System-assigned (turn on) |
| C | **Foundry account MI** | Foundry / AI Services account → Identity → System-assigned |
| D | **Foundry project MI** | Foundry project resource → Identity → System-assigned |

#### A — You (running the scripts)

| Role | Scope | Why |
|---|---|---|
| `Storage Blob Data Contributor` | Storage account | Upload sample HR docs to the blob container |
| `Search Service Contributor` | AI Search service | Create/update index, data source, skillset, indexer |
| `Search Index Data Reader` | AI Search service | Query the index from local code in script 3 |
| `Foundry User` (AI Services account) **or** `Cognitive Services OpenAI User` (plain Azure OpenAI account) | Foundry / AOAI resource | Optional — only if you also call the chat / embedding deployments locally |

#### B — AI Search service managed identity

| Role | Scope | Why |
|---|---|---|
| `Storage Blob Data Reader` | Storage account | Indexer reads blobs via MI (no connection string keys) |
| `Foundry User` (AI Services account) **or** `Cognitive Services OpenAI User` (plain AOAI) | Foundry / AOAI resource | Skillset + vectorizer call the embedding deployment via MI |

#### C — Foundry account managed identity

No role assignments required for this knowledge-base scenario. (Account MI is
for management-plane operations; agents and KB connections live at the
project level.)

#### D — Foundry project managed identity

| Role | Scope | Why |
|---|---|---|
| `Search Service Contributor` | AI Search service | Lets the Foundry portal **list indexes** when you click "+ Add knowledge → Azure AI Search" |
| `Search Index Data Reader` | AI Search service | Lets the agent **query documents** from the index at runtime |
| `Storage Blob Data Reader` | Storage account | Optional — only if you ever attach blobs directly via the Foundry "Files" feature |

> Why two roles on Search for the project MI? Azure AI Search splits
> control plane (list/manage indexes) from data plane (query/index docs).
> No single built-in role except `Owner` covers both, and `Owner` on a
> service identity is overkill.

> **Foundry vs. plain Azure OpenAI.** Foundry / AI Services accounts
> (`kind = AIServices`, endpoint `*.services.ai.azure.com`) use the
> `Foundry User` role, whose data action `Microsoft.CognitiveServices/*`
> covers embeddings and chat. Plain Azure OpenAI accounts
> (`kind = OpenAI`, endpoint `*.openai.azure.com`) use the more granular
> `Cognitive Services OpenAI User` role.

### Troubleshooting RBAC and indexing

Quick checks if something doesn't work:

- **Cognitive Services data-plane RBAC takes 10–15 minutes to propagate.**
  Wait, then re-test. Don't keep re-running the indexer immediately after a
  role change.
- **After any RBAC, skillset, or skill change, always Reset → Run the
  indexer**, not just Run. The indexer's blob change-detection records
  blobs as "seen" even when a downstream skill (like embedding) failed,
  so a plain re-run will silently skip them.
- **An indexer can report `success` with `itemsProcessed: N` and still leave
  the index empty.** Always verify with `GET /indexes/{name}/stats` —
  `documentCount` should be > 0.
- If the Foundry "+ Add knowledge → Azure AI Search" dialog shows the
  service but **no indexes**, the project MI is missing
  `Search Service Contributor` on the search service.
- If the dialog says **"missing semantic configuration"**, the index needs
  a semantic configuration (Foundry IQ requires one). `create-knowledge-base.py`
  already defines this; if you built the index manually, add a `SemanticSearch`
  block with one configuration that prioritizes a title field and at least
  one content field.
- If the indexer's key field error mentions
  *"index key field must have the keyword analyzer set"*, the chunk-id key
  field must be a `SearchableField` with `analyzer_name="keyword"` —
  `SimpleField` silently ignores `analyzer_name`.

## Setup

```powershell
# 1. Clone and enter the repo
git clone <your-fork-url> maf-demos
cd maf-demos

# 2. Create the venv and install pinned dependencies from uv.lock
uv sync

# 3. Configure environment variables
copy .env.example .env
# then edit .env with your own resource names
```

## Run the demos

```powershell
# Stage 1 - local Ollama model, no cloud
uv run .\1-local-model.py

# Stage 2 - Foundry-hosted chat model
uv run .\2-foundry-model.py

# One-time: build the index + Foundry IQ knowledge base
uv run .\create-knowledge-base.py

# Stage 3 - agent grounded in the KB via the MCP endpoint
uv run .\3-foundry-iq.py

# One-time: register the Foundry Toolbox in your project
uv run .\create-toolbox.py

# Stage 4 - agent with KB + toolbox (web search + code interpreter)
uv run .\4-toolbox.py

# Stage 5 - run the agent as a Foundry Hosted Agent server (see section below)
uv run .\5-hosted-agent.py
```

> On Windows, set the console to UTF-8 before running scripts that print model output
> (`chcp 65001; $env:PYTHONIOENCODING="utf-8"`) — otherwise Unicode characters
> like the minus sign or em-dash crash the legacy cp1252 console.

### Test the hosted agent locally

`5-hosted-agent.py` is a web server (Foundry Hosted Agent runtime).
Start it in one terminal, then send a request from another:

```powershell
# Terminal 1 - start the server (default: http://localhost:8088)
uv run .\5-hosted-agent.py

# Terminal 2 - send a sample request and print only assistant text + tool calls
uv run .\test-hosted-agent-locally.py
```

Health check: `curl http://localhost:8088/readiness` should return `200`.

## Deploy as a Foundry Hosted Agent (VS Code extension)

Once the agent works locally, you can ship the same `5-hosted-agent.py`
container to **Microsoft Foundry Hosted Agents**. This repo uses the
**Microsoft Foundry VS Code extension** for the deploy step (no `azd` CLI
required).

### Files that drive the deploy

| File | Purpose |
|---|---|
| [Dockerfile](Dockerfile) | Builds a `python:3.13-slim` image, installs deps with `uv sync --locked --no-dev`, copies `5-hosted-agent.py`, exposes 8088, runs the script. |
| [.dockerignore](.dockerignore) | Keeps the build context lean — excludes `.venv/`, `data/`, the other demo scripts, `.env*`, etc. |
| [agent.yaml](agent.yaml) | The hosted-agent manifest the extension reads (kind, protocols, resources, dockerfile path, non-secret env vars). |

### Step-by-step

1. Install the **Microsoft Foundry** VS Code extension and sign in to your
   tenant.
2. From the extension sidebar, open your Foundry project → **Agents**.
3. Right-click and choose **Deploy New Agent** (first deploy) or
   **Deploy New Version** (subsequent deploys). Pick [agent.yaml](agent.yaml)
   when prompted.
4. The extension ships the build context to a remote builder, produces an
   image, and registers a new agent version.
5. Wait for the version to show **Active** in the agents list, then
   right-click → **View Logs** to watch the container start. You should see:
   ```
   AgentServerHost starting on 0.0.0.0:8088
   ```
6. Right-click the agent → **Test** to send a sample prompt.

### Required role assignments for the hosted agent's identity

Each deployed agent version gets its **own workload identity** (separate
from yours and from the Foundry project MI). Every downstream service the
agent calls must grant that identity access. Look up the agent's
principal id from the agent details pane in the extension, or via:

```powershell
az rest --method GET `
  --url "<your-project-endpoint>/agents/<your-agent-name>?api-version=v1" `
  --resource "https://ai.azure.com" `
  --query "instance_identity.principal_id" -o tsv
```

Then assign:

| Role | Scope | Why |
|---|---|---|
| `Foundry User` (Foundry / AI Services) **or** `Cognitive Services OpenAI User` (plain AOAI) | Foundry / AOAI resource | Agent calls the chat model |
| `Search Index Data Reader` | AI Search service | Agent queries the KB via MCP at runtime |

> Data-plane role assignments on Cognitive Services and AI Search take
> **10–15 minutes** to propagate. Wait before retrying.

### Things that bit us — and the fixes

- **`agent.yaml` schema mismatch.** The hosted-agent CLI/extension expects
  flat top-level keys (`kind`, `name`, `protocols`, `resources`,
  `dockerfile_path`, `environment_variables`). Nesting them under a
  `definition:` block is the **REST API** schema; the tooling silently
  ignores it, so the container starts with no env vars and you get a
  `KeyError: 'AZURE_AI_MODEL_DEPLOYMENT_NAME'` on import. See the comments
  at the top of [agent.yaml](agent.yaml).

- **401 from the model after a clean deploy.** The agent's workload
  identity hadn't been granted `Foundry User` on the Foundry project.
  Local works because your CLI user already has access; the hosted
  identity is brand new. Fix: assign the role (see table above) and wait
  for propagation.

- **403 from the knowledge base MCP after the model was working.** Same
  pattern as above, different resource — assign `Search Index Data Reader`
  on the AI Search service to the agent identity.

- **`ImportError: cannot import name 'Agent' from 'agent_framework'`
  inside the container.** The deployed image was stale — a cached layer
  installed an older `agent-framework-core` that doesn't export `Agent`
  at the top level. Fix: refresh the lockfile, force a fresh build, and
  verify locally before redeploying:

  ```powershell
  uv lock
  docker build --no-cache --platform linux/amd64 -t hosted-agent-check .
  docker run --rm hosted-agent-check python -c "from agent_framework import Agent; print('ok')"
  ```

  Then redeploy from the extension. (`agent-framework==1.3.0` is a
  meta-package that only ships `agent_framework_meta/`. The real
  `agent_framework/__init__.py` is provided by `agent-framework-core==1.3.0`.)

- **Secrets in `agent.yaml`.** `environment_variables` is for
  **non-secret** config only — endpoint URLs, deployment names, resource
  names. The file is checked into source control. Anything secret should
  be added as a **CustomKeys connection** on the Foundry project and
  referenced from `agent.yaml` as
  `${{connections.<connection-name>.credentials.<field>}}`.

## Project layout

```
.
├── 1-local-model.py              # Stage 1: local SLM via Ollama
├── 2-foundry-model.py            # Stage 2: Foundry chat model
├── 3-foundry-iq.py               # Stage 3: agent + KB MCP tool
├── 4-toolbox.py                  # Stage 4: agent + KB MCP + Foundry Toolbox
├── 5-hosted-agent.py             # Stage 5: agent wrapped as Foundry Hosted Agent server
├── create-knowledge-base.py      # One-time: build index + KB
├── create-toolbox.py             # One-time: register the Foundry Toolbox
├── test-hosted-agent-locally.py  # Sample client for the stage 5 server
├── Dockerfile                    # Container image for the hosted agent
├── .dockerignore                 # Build-context exclusions
├── agent.yaml                    # Foundry Hosted Agent manifest (used by the VS Code extension)
├── data/hr-kb/                   # Sample HR markdown / CSV documents
├── .env.example                  # Template for required env vars
├── pyproject.toml                # Project + pinned dependencies
└── uv.lock                       # Locked transitive dependency graph
```

## Dependency management

This project uses `uv` for dependency management. Common commands:

```powershell
uv sync                  # install exact versions from uv.lock
uv add <package>         # add and lock a new dependency
uv remove <package>      # drop a dependency
uv lock --upgrade        # refresh the lock to newer versions
uv run <script.py>       # run inside the synced venv (no manual activation)
```

Commit `pyproject.toml` and `uv.lock`. Do not commit `.venv/` or `.env`.

## Notes

- Authentication everywhere uses `AzureDeveloperCliCredential` /
  `DefaultAzureCredential` — no API keys are stored in code or `.env`.
- Script 3 talks to the KB via MCP (streamable HTTP). The MCP URL targets
  the Foundry IQ `KnowledgeBase` resource created by `create-knowledge-base.py`,
  not the bare search index.
- Script 4 runs the **Foundry Toolbox** (`web_search` + `code_interpreter`)
  side-by-side with the **direct KB MCP** from script 3. The KB tool is
  intentionally NOT registered through the toolbox — see the
  [Foundry Toolbox + KB caveat](#foundry-toolbox--kb-caveat) below.
- Script 5 reuses the same agent definition as script 4 but wraps it in
  `ResponsesHostServer` from `agent-framework-foundry-hosting`, exposing the
  OpenAI Responses API on `http://localhost:8088`.
- The HR scenarios, company name, and benefit details in `data/hr-kb/` are
  fictional sample content for demo purposes only.

### Foundry Toolbox + KB caveat

At the time of writing, registering the Azure AI Search KB MCP server inside
a Foundry Toolbox does not work end-to-end:

- The toolbox MCP gateway accepts the client connection and lists its own
  tools fine.
- When it forwards `tools/list` to the registered KB MCP server, it strips
  the `?api-version=2025-11-01-Preview` query string from the session URL.
- Azure AI Search then returns `400 — Invalid or missing api-version query
  string parameter`.

Workaround used in this repo:

- `create-toolbox.py` registers only `web_search` and `code_interpreter`
  in the toolbox (the KB block is left in place but commented out, with a
  note explaining why).
- `4-toolbox.py` opens **two** MCP tools concurrently: the toolbox MCP for
  web search / code interpreter, and the KB MCP directly (same pattern as
  `3-foundry-iq.py`). Both are passed to the agent.

When the gateway bug is fixed, you can move the KB tool back into the
toolbox by uncommenting the `mcp` entry in `create-toolbox.py`,
re-registering, and removing the direct KB MCP tool from `4-toolbox.py`.