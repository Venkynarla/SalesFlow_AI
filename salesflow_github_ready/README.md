# SalesFlow AI Dashboard

GitHub-ready version extracted from the Colab notebook.

## What this app contains

- FastAPI backend
- Single-page dashboard in `frontend/index.html`
- Campaign and contact APIs
- Manual enrichment field support
- NVIDIA OpenAI-compatible AI drafting
- Email sending service
- Background follow-up scheduler
- Playwright-based enrichment attempt

## Project structure

```text
salesflow-ai/
├── backend/
│   ├── models/
│   ├── routers/
│   └── services/
├── frontend/
│   └── index.html
├── main.py
├── requirements.txt
├── Dockerfile
├── render.yaml
├── .env.example
└── .gitignore
```

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate   # Mac/Linux
# .venv\Scripts\activate  # Windows

pip install -r requirements.txt
playwright install chromium

cp .env.example .env
# edit .env and add your NVIDIA_API_KEY

uvicorn main:app --reload --port 8000
```

Open:

```text
http://localhost:8000
```

Health check:

```text
http://localhost:8000/api/health
```

## Push to GitHub

```bash
cd salesflow-ai

git init
git add .
git commit -m "Initial SalesFlow AI app"

git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/salesflow-ai.git
git push -u origin main
```

## Deploy free on Render

1. Go to Render.
2. Create **New Web Service**.
3. Connect your GitHub repo.
4. Select **Docker** environment.
5. Use free plan.
6. Add environment variables:
   - `NVIDIA_API_KEY`
   - `NVIDIA_MODEL=meta/llama-3.1-8b-instruct`
   - `DATABASE_URL=sqlite:///./sales_automation.db`
   - `EMAIL_DEV_MODE=true`
   - `SMTP_USER`
   - `SMTP_PASSWORD`
   - `SMTP_HOST=smtp.gmail.com`
   - `SMTP_PORT=587`
   - `SENDER_NAME=Venkat`
7. Deploy.

Your app URL will look like:

```text
https://salesflow-ai.onrender.com
```

## Important notes

Render free tier gives a permanent URL, but the app may sleep after inactivity. When you open it again, it will wake up automatically.

SQLite storage on free hosting is not ideal for long-term production. For real campaigns, move to Supabase/Postgres later and update `DATABASE_URL`.

LinkedIn scraping may be blocked frequently. Use manual enrichment or a compliant enrichment API for reliable production results.
