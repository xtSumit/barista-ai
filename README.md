# AI Barista — ADK + RAG on Cloud Run

A personalized barista agent for a coffee shop. Built with Google's
**Agent Development Kit (ADK)** and **Gemini**, grounded by **RAG** in a
`menu.json` dataset, served as a **Streamlit** chat app, and deployed to
**Cloud Run**.

Google Cloud Gen AI Academy APAC (Hack2Skill) — Track 1, Lab 1.

## What it does

Ask for a coffee the way you'd ask a person — "something strong but not bitter",
"a sweet iced drink", "I'm lactose intolerant, what can I have?" — and the agent
retrieves matching drinks from the menu before answering. It recommends only
drinks that are actually on the menu, quotes real prices, and filters out
anything containing an allergen you've mentioned.

## How the RAG works

```
you type  →  Streamlit (app.py)
                 │
                 ▼
          ADK LlmAgent (agent.py) ── decides it needs menu data
                 │
                 ▼
          search_menu() tool (menu_tool.py)
                 │  keyword-scores name/description/tags,
                 │  drops anything with a blocked allergen
                 ▼
          matching drinks as JSON  →  back into Gemini's context
                 │
                 ▼
          grounded recommendation  →  Streamlit chat
```

Retrieval is a keyword scan over 8 items rather than a vector index — at this
corpus size exact matching retrieves as well as embeddings would, with no
database and no embedding call per query.

## Files

| File | Purpose |
|---|---|
| `menu.json` | The RAG data source — 8 drinks with tags, allergens, price, caffeine |
| `menu_tool.py` | `search_menu()` — retrieval + allergen filtering. No ADK import, so it's testable offline |
| `agent.py` | The ADK `LlmAgent`, its instruction, and the tool wiring |
| `app.py` | Streamlit chat UI, conversation history, ADK runner |
| `test_menu.py` | Offline checks on retrieval and allergen safety |
| `requirements.txt` | Pinned dependencies, used by the Cloud Run buildpack |

## Run it locally (Windows)

```powershell
cd ai-barista-adk

# 1. Virtual environment
py -3.13 -m venv venv
.\venv\Scripts\Activate.ps1

# 2. Dependencies
pip install -r requirements.txt

# 3. Your Gemini key — get one at https://aistudio.google.com/apikey
Copy-Item .env.example .env
notepad .env       # paste the key into GOOGLE_API_KEY, save, close

# 4. Offline sanity check (no key needed)
python test_menu.py

# 5. Run
streamlit run app.py
```

Opens at http://localhost:8501.

### Try these

- `I want something strong and iced` → should land on Cold Brew
- `I'm lactose intolerant but want something sweet` → must not offer the
  Cappuccino, Iced Vanilla Latte, Mocha or Affogato
- `Do you have anything with hazelnut?` → Hazelnut Mocha, flagged for nuts
- `Something caffeine-free for the evening` → Decaf Soy Caramel Macchiato

## Authentication — one codebase, two environments

No code branches on environment. The `google-genai` client reads its backend
from environment variables, so the same `agent.py` runs in both places.

| | Local | Cloud Run |
|---|---|---|
| Backend | Gemini API (AI Studio) | Vertex AI |
| Credential | `GOOGLE_API_KEY` in `.env` | The runtime service account |
| Set by | `python-dotenv` at startup | `--set-env-vars` at deploy time |

Locally there's no gcloud CLI, so Application Default Credentials aren't
available — hence the API key. On Cloud Run the service account is available for
free, so no key needs to exist in the deployed config at all.

`.env` is gitignored. No credential is ever committed.

`GOOGLE_GENAI_USE_ENTERPRISE` is the current name for this switch;
`GOOGLE_GENAI_USE_VERTEXAI` is the older alias and still works. If both are set,
the SDK uses `ENTERPRISE` and warns about the conflict.

### Models

The default is `gemini-3.5-flash`. Override with `BARISTA_MODEL` — no code change.
`gemini-2.5-flash` is retired for new API keys, and free-tier quota is *per model*
— `gemini-3.6-flash` and `gemini-3.7-flash` both throttled during testing while
`3.5-flash` stayed available. If messages start failing, this is the first knob to
turn: `BARISTA_MODEL=gemini-3.7-flash streamlit run app.py`.

## Deploy to Cloud Run (from Google Cloud Shell)

Run these in Cloud Shell after `git clone`-ing this repo and `cd`-ing into it.
Source-based deploy — Cloud Buildpacks build the container, no Dockerfile.

```bash
# ── 1. Point gcloud at your project ────────────────────────────────────────
export PROJECT_ID="your-project-id"      # <-- edit this
export REGION="us-central1"
gcloud config set project "$PROJECT_ID"

# ── 2. Enable the APIs this needs ──────────────────────────────────────────
#   run              serves the app
#   cloudbuild       builds the container from source
#   artifactregistry stores the built image
#   aiplatform       Vertex AI, i.e. Gemini itself
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  aiplatform.googleapis.com

# ── 3. A service account for the running app ───────────────────────────────
# This is the identity the container authenticates to Gemini as. Its own
# identity replaces the API key entirely once deployed.
gcloud iam service-accounts create barista-agent-sa \
  --display-name="AI Barista Cloud Run runtime"

# ── 4. Let that identity call Gemini ───────────────────────────────────────
# Without this the app deploys green and then 403s on the first message.
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:barista-agent-sa@$PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/aiplatform.user"

# ── 5. Deploy ──────────────────────────────────────────────────────────────
# --source .          build from this directory with buildpacks, no Dockerfile
# --allow-unauthenticated   public URL, which the submission requires
# --command/--args     the buildpack's default entrypoint doesn't know about
#                      Streamlit; this tells it to bind Cloud Run's $PORT.
#                      CORS/XSRF are disabled because Cloud Run terminates TLS
#                      at the proxy and Streamlit otherwise rejects the request.
# --set-env-vars       switches the SDK from API-key mode to Vertex AI mode
gcloud run deploy ai-barista \
  --source . \
  --region "$REGION" \
  --allow-unauthenticated \
  --service-account "barista-agent-sa@$PROJECT_ID.iam.gserviceaccount.com" \
  --command "/cnb/lifecycle/launcher" \
  --args "sh,-c,python3 -m streamlit run app.py --server.port=\$PORT --server.address=0.0.0.0 --server.enableCORS=false --server.enableXsrfProtection=false" \
  --set-env-vars "GOOGLE_GENAI_USE_ENTERPRISE=TRUE,GOOGLE_CLOUD_PROJECT=$PROJECT_ID,GOOGLE_CLOUD_LOCATION=global"
```

The command prints a `https://ai-barista-....run.app` URL. That URL is the
deliverable.

### If the deploy fails

- **Build fails on permissions** — the Cloud Build service account may need
  `roles/storage.admin`; Cloud Shell usually prompts with the exact grant.
- **App deploys but the page never loads** — almost always the entrypoint. Check
  `gcloud run services logs read ai-barista --region "$REGION"` for whether
  Streamlit bound `$PORT`.
- **First message 403s** — step 4 didn't apply, or it needs a minute to propagate.
- **404 on the model** — override it without touching code:
  add `,BARISTA_MODEL=<a-model-you-have-access-to>` to `--set-env-vars`.
- **429 / 503 on every message** — free-tier quota, not a bug. Vertex AI on Cloud
  Run uses project quota rather than the AI Studio free tier, so this is mostly a
  local-testing problem. Wait a minute, or switch `BARISTA_MODEL`.
