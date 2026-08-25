**# 🧠 MeetPlus AI**

\`\`\`

$ whoami

\> MeetPlus AI — Intelligent Meeting Action-Item Extractor

$ tagline --show

\> "Turn messy meetings into organized action."

\`\`\`

[Live Demo — Add after deployment]

**---**

**## ☁️ Shared Cloud Workspace Setup (Supabase)**

This version stores shared workspace data in Supabase so team members can create a workspace, share its code and password, and see the same meeting history from different devices.

1\. Create a free Supabase project.

2\. Open **\*\*SQL Editor\*\*** and run the complete contents of \`supabase\_schema.sql\`.

3\. In the Supabase project settings, copy the Project URL and your publishable/anon key.

4\. Add these to Streamlit secrets locally and in Streamlit Community Cloud:

\`\`\`toml

GEMINI\_API\_KEY = "your\_key"

SUPABASE\_URL = "https\://your-project.supabase.co"

SUPABASE\_KEY = "your\_publishable\_or\_anon\_key"

\`\`\`

Never commit a real \`.streamlit/secrets.toml\` file.



**## 📖 Overview**

MeetPlus AI is a Streamlit + Gemini capstone application built for

**\*\*MirAI School of Technology\*\***. It solves \*\*Project #20 — Meeting

Action-Item Extractor\*\*:

\> Users paste a chaotic raw transcript from a 1-hour meeting. The AI

\> parses the text into an interactive \`st.data\_editor\` table assigning

\> tasks, deadlines, and owners.

Paste (or upload) a raw, messy transcript, and MeetPlus AI turns it into

an executive summary, key decisions, a fully editable action-item table,

live analytics, and downloadable reports — without ever inventing an

owner, deadline, or decision that wasn't actually in the transcript.

**## 📸 Project Screenshots**

**### 🔐 Login**

![Login]\(Screenshots/login.png)

**### 📊 Dashboard**

![Dashboard]\(Screenshots/dashboard.png)

**### 🎙️ Analyze Meeting**

![Analyze Meeting]\(Screenshots/analyze-meeting.png)

**### 📝 Meeting Summary**

![Meeting Summary]\(Screenshots/meeting-summary.png)

**### 📋 Action Items**

![Action Items]\(Screenshots/action-items.png)

**### 🧠 AI Intelligence**

![AI Intelligence]\(Screenshots/ai-intelligence.png)

**### 📈 Analytics**

![Analytics]\(Screenshots/analytics.png)

**### 🔎 Meeting Search**

![Meeting Search]\(Screenshots/meeting-search.png)

**---**

**## ❓ Problem Statement**

Meeting notes are usually informal, unstructured, and full of

interruptions. Important commitments get lost. MeetPlus AI applies an LLM

(Gemini) with a strict, specialized system instruction to reliably surface

*\*only\** what was actually said — flagging unknowns explicitly instead of

guessing.

**---**

**## ✨ Features**

\`\`\`

$ features --list

\> ✔ Meeting metadata form (title, date, team, participants)

\> ✔ Paste transcript OR upload a .txt file

\> ✔ Gemini-powered structured extraction (JSON schema enforced)

\> ✔ Executive summary, key decisions, risks, follow-up questions

\> ✔ Editable st.data\_editor table (dropdowns for Priority / Status)

\> ✔ Live KPI dashboard (st.metric): total, high priority, unassigned,

\>   overdue, completion %

\> ✔ Search + filter (owner, priority, status, overdue-only)

\> ✔ Plotly analytics: by owner, by priority, by status, deadline spread

\> ✔ AI Extraction Quality panel (AI-estimated confidence, ambiguous items)

\> ✔ In-session meeting history (no database required)

\> ✔ CSV / JSON / Markdown export

\> ✔ Zero hard-coded API keys — env var or Streamlit secrets only

\`\`\`

**---**

**## 🧱 Tech Stack**

\`\`\`

$ stack --show

\> Frontend/App ......... Streamlit

\> AI Engine ............ Google Gemini API (google-genai SDK)

\> Data Processing ...... Pandas

\> Visualization ........ Plotly

\> Deployment ........... Streamlit Community Cloud

\> Language ............. Python 3.11+

\`\`\`

**---**

**## 🏗️ Architecture**

\`\`\`mermaid

flowchart TD

    U[User] --> UI[Streamlit UI]

    UI --> FORM[st.form: meeting details + transcript]

    FORM --> T[Transcript text]

    T --> GC[utils/gemini\_client.py]

    GC --> API[Gemini API - structured JSON]

    API --> VAL[Validation and safe defaults]

    VAL --> SS[(st.session\_state)]

    SS --> DF[Pandas DataFrame]

    DF --> DASH[Dashboard: metrics, charts]

    DF --> EDIT[st.data\_editor]

    EDIT --> SS

    DF --> EXP[CSV / JSON / Markdown export]

\`\`\`

Full diagram and component breakdown: [\`docs/architecture.md\`]\(docs/architecture.md)

**---**

**## 📂 Project Structure**

\`\`\`

MeetPlus-AI/

│

├── app.py                          # Main Streamlit application

├── requirements.txt

├── README.md

├── .gitignore

├── .env.example                    # Template only — no real key

│

├── .streamlit/

│   └── secrets.toml.example        # Template only — no real secret

│

├── data/

│   ├── college\_project\_meeting.txt

│   ├── software\_sprint.txt

│   └── marketing\_review\.txt

│

├── utils/

│   ├── \_\_init\_\_.py

│   ├── gemini\_client.py            # System prompt, dynamic context, API call, validation

│   ├── analytics.py                # Pandas pipeline, KPIs, Plotly charts

│   └── export.py                   # CSV / JSON / Markdown export

│

├── docs/

│   ├── architecture.md

│   └── technical\_design.md

│

└── assets/

    └── README.md

\`\`\`

**---**

**## ⚙️ Installation**

\`\`\`

$ git clone \<your-repo-url>

$ cd MeetPlus-AI

$ python -m venv .venv

$ source .venv/bin/activate      # Windows: .venv\Scripts\activate

$ pip install -r requirements.txt

\`\`\`

**---**

**## 🔑 Gemini API Setup**

1\. Get a key from [Google AI Studio]\(https\://aistudio.google.com/).

2\. **\*\*Local development\*\*** — copy the example env file and fill in your key:

   \`\`\`

   $ cp .env.example .env

   \`\`\`

   Then edit \`.env\`:

   \`\`\`

   GEMINI\_API\_KEY=your\_real\_key\_here

   \`\`\`

   \`.env\` is already listed in \`.gitignore\` — it will never be committed.

3\. **\*\*Streamlit Cloud\*\*** — copy \`.streamlit/secrets.toml.example\` into the

   app's **\*\*Settings → Secrets\*\*** panel on Streamlit Community Cloud (do not

   commit a real \`secrets.toml\`).

The app checks, in order: the \`GEMINI\_API\_KEY\` environment variable, then

\`st.secrets["GEMINI\_API\_KEY"]\`. If neither is set, the sidebar shows a

clear "No Gemini API key found" status instead of crashing.

**---**

**## ▶️ Run Locally**

\`\`\`

$ export GEMINI\_API\_KEY=your\_real\_key\_here   # or use .env with python-dotenv

$ streamlit run app.py

\`\`\`

Then open the URL Streamlit prints (typically \`http\://localhost:8501\`).

**---**

**## ☁️ Deployment (Streamlit Community Cloud)**

\> Deployment has **\*\*not\*\*** been performed as part of building this codebase.

\> Follow these steps yourself:

1\. Push this repository to GitHub (see commands below).

2\. Go to [share.streamlit.io]\(https\://share.streamlit.io) and sign in.

3\. Click **\*\*"New app"\*\***, select your repo, branch, and \`app.py\` as the

   entry point.

4\. Under **\*\*Advanced settings → Secrets\*\***, paste:

   \`\`\`

   GEMINI\_API\_KEY = "your\_real\_key\_here"

   \`\`\`

5\. Click **\*\*Deploy\*\*** and wait for the build to finish.

6\. Test the live app end-to-end, then replace the placeholder at the top

   of this README with the real URL.

**---**

**## 🧠 Prompt Engineering Strategy**

\- Gemini is given a persona-locked system instruction ("MeetPlus AI, an

  enterprise meeting-intelligence and action-item extraction engine") with

  11 explicit rules, not a freeform prompt.

\- Hallucination safeguards are stated directly and repeatedly: never

  invent an owner, deadline, or decision; use \`"Unassigned"\` /

  \`"Not specified"\` when unknown; prefer omission to fabrication.

\- Dynamic context (title, date, team, participants, transcript, *\*and\**

  today's date) is injected per-call via Python f-strings so relative

  deadlines like "next Monday" can be resolved sensibly.

\- Output is constrained with a JSON \`response\_schema\` at the API level,

  then re-validated in Python (\`validate\_and\_normalize\`) so a malformed or

  partial response can never crash the app.

Full detail: [\`docs/technical\_design.md\`]\(docs/technical\_design.md)

**---**

**## 🔄 Data Flow**

\`Transcript → Gemini (structured JSON) → validation/defaults → Pandas

DataFrame → st.session\_state → Dashboard / st.data\_editor / Analytics /

Export\`. The Gemini API is called exactly once per form submission — never

on a filter change, chart interaction, or table edit.

**---**

**## 🔒 Security**

\- No API key is ever hard-coded in source.

\- \`.env\` and \`.streamlit/secrets.toml\` are git-ignored.

\- Only \`.env.example\` and \`secrets.toml.example\` (placeholder values) are

  committed.

**---**

**## 🛡️ Error Handling**

Empty/short transcripts, missing API keys, Gemini API failures, malformed

JSON, missing fields, invalid dates, and duplicate tasks are all handled

gracefully with user-facing messages — the app is designed to never show a

raw stack trace to a normal user. See §9 of

[\`docs/technical\_design.md\`]\(docs/technical\_design.md) for the full list.

**---**

**## 🧪 Testing**

What was actually done before delivery:

\- \`python -m py\_compile\` on every Python file (syntax check) — passed.

\- Manual cross-check of every import against \`requirements.txt\`.

\- Manual review of module boundaries between \`app.py\` and \`utils/\*.py\`.

What could **\*\*not\*\*** be verified in the build environment (no network / no

API key available there) and is left for you to confirm locally:

\- An actual end-to-end Gemini API call and response.

\- A live \`streamlit run app.py\` session in a browser.

\- Streamlit Cloud deployment itself.

**---**

**## 🚀 Future Improvements**

\- Persistent (database-backed) meeting history across sessions.

\- Multi-language transcript support.

\- Direct export to Slack / Notion / Jira.

\- Speaker-level attribution in the extracted context field.

**---**

\`\`\`

$ status --final

\> Core app: implemented

\> AI integration: implemented

\> Dashboard / data editor / analytics: implemented

\> Deployment: pending (student action required)

\> Live demo: [Live Demo — Add after deployment]

\`\`\`



**## 🚀 Upgraded Features**

\- AI Meeting Intelligence Center: effectiveness score, sentiment, risk level, strengths, improvements, and next agenda.

\- Ask the Meeting: transcript-grounded Gemini Q&A.

\- AI Follow-up Email Generator.

\- Rule-based Meeting Health Score.

\- Workload Balance Analytics.

\- Plain-text report export in addition to CSV, JSON, and Markdown.



**## 🎨 Theme Studio & Productivity Upgrades**

MeetPlus AI now includes a Theme Studio with Midnight, Cyberpunk, Ocean, Sakura, Forest, Sunset, Terminal, Minimal, Glassmorphism, and Custom Accent styles. The dashboard also includes an Action Tracker, Team Dashboard, Meeting Search, and AI Chat with Meeting History.

**### 🧭 New workflow**

1\. Pick a visual style from the sidebar Theme Studio.

2\. Record/upload audio or paste a transcript.

3\. Extract action items and update their statuses.

4\. Review team workload and task completion.

5\. Search processed meetings or ask Gemini questions across saved meeting history.



\> Meeting history is session-based unless a database is added.