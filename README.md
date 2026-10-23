# CareCircle: an Alexa+ companion for elderly people living alone

> **Alexa+ track · AWS Builder mini challenge.** A self-hosted **MCP server (spec 2025-11-25, Streamable HTTP)**
> that lets an elderly parent manage medications, appointments and daily wellbeing just by talking to Alexa+.
> A **LangGraph Care Risk Agent** on **Amazon Bedrock** spots worrying patterns and keeps the family informed.

Millions of elderly people live alone while their children live in other cities. The children worry and call
every day, but a phone call can't tell you that Mum has skipped her evening tablet three times this week and has
also mentioned feeling dizzy twice. CareCircle notices that kind of pattern.

```
 "I feel dizzy again today"
          │  voice
          ▼
   Alexa+ (simulated web client)  ──MCP / Streamable HTTP──►  CareCircle MCP server
                                                                ├─ 11 tools (meds, wellbeing, appointments, family)
                                                                ├─ LangGraph Care Risk Agent  ─► Amazon Bedrock (Claude)
                                                                ├─ Safety judge (LLM-as-Judge) on every spoken reply
                                                                ├─ Prescription photo → schedule (Bedrock vision)
                                                                └─ DynamoDB · SNS (SMS/email to family)
                                                                          │
                                                                          ▼
                                                     Family dashboard: alerts, adherence, AI weekly summary
```

## What it does

| For the parent (by voice) | For the family |
|---|---|
| "What do I need to take today?" | Instant SMS/email when something is wrong |
| "I just took my BP tablet" (fuzzy matched, no drug names needed) | Live dashboard: adherence, mood timeline, alerts with evidence |
| "I feel dizzy again" → logged and risk-assessed | "How is Mum doing?" via the `family_status` tool |
| "I fell in the bathroom" → **family alerted immediately** + emergency guidance | AI weekly summary with suggested actions |
| "Tell my son to call me tonight" | Snap a prescription → medication schedule created automatically |

## How it works

**MCP tools:** `get_today_plan`, `log_medication`, `get_medication_schedule`, `add_medication`,
`log_wellbeing`, `get_appointments`, `add_appointment`, `alert_family`, `family_status`, `weekly_summary`,
`import_prescription`. All tools have typed input schemas, **structured output**, and MCP tool annotations
(`readOnlyHint` / `openWorldHint`).

**Care Risk Agent (LangGraph):** `gather → detect → (escalate | reason) → act`
- **Hard safety rules come first.** Red-flag phrases (a fall, chest pain, trouble breathing, slurred speech…)
  escalate to an **urgent** family alert without any model in the loop, so safety never depends on an LLM call.
- **Softer patterns** (recurring symptoms, missed doses in the last 48h, low-mood streaks, no check-in, and especially
  a *symptom combined with missed medication*) go to **Bedrock (Claude)**. It decides `none / nudge / alert` and
  writes a kind, evidence-based message for the family.
- **De-duplication** means the family isn't paged twice for the same issue within 12 hours.

**Safety judge (LLM-as-Judge):** every spoken reply is checked by deterministic guards (no dose-change or
diagnosis advice, emergency guidance added when red flags are present) and then by a Bedrock judge scoring
*safety · groundedness in tool results · clarity for an elderly listener*. Replies that fail are rewritten
before Alexa speaks them. Try: *"Can I take two metformin tonight since I missed one?"*

**Prescription vision:** upload a photo of a prescription. Bedrock reads the medications, converts "BD / TDS / HS"
into clock times, adds them to the schedule, and flags low-confidence readings for a human to check.

**Runs with or without the cloud:** `CARECIRCLE_LLM=offline` gives a rule-based mode with zero setup.
Point the same code at Bedrock, DynamoDB and SNS by changing environment variables.

## Quick start

```bash
git clone <this repo> && cd carecircle
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                   # edit as needed
python -m carecircle.server
```
- Alexa+ simulator (voice in Chrome): http://127.0.0.1:8000/alexa
- Family dashboard: http://127.0.0.1:8000/dashboard
- **MCP endpoint:** http://127.0.0.1:8000/mcp (Streamable HTTP)

Inspect it with the official MCP Inspector: `npx @modelcontextprotocol/inspector` → Transport *Streamable HTTP* → URL above.

### Using AWS
1. `aws configure` (region `us-east-1`) with an IAM user allowed to use Bedrock, DynamoDB and SNS.
2. Enable Anthropic Claude in the Bedrock console (one-time use-case form).
3. Optional: create DynamoDB table `carecircle` (partition key `pk` String, sort key `sk` String) and set
   `CARECIRCLE_STORAGE=dynamodb`.
4. Optional: create an SNS topic, subscribe your phone/email, and set `CARECIRCLE_SNS_TOPIC_ARN`.

### Deploy to AWS (from AWS CloudShell)
```bash
bash deploy/setup_aws.sh you@example.com   # DynamoDB table, SNS topic + email subscription, Bedrock model check, .env
bash deploy/deploy_ec2.sh                   # S3 bundle -> EC2 (IAM role, systemd) -> CloudFront HTTPS
```
The EC2 security group only accepts traffic from CloudFront's origin-facing prefix list.

### LLM providers
`CARECIRCLE_LLM=bedrock` is the primary path (Converse API with tool use, vision). If Bedrock isn't available on your
account, set `OPENAI_API_KEY` (plus `OPENAI_BASE_URL` / `CARECIRCLE_OPENAI_MODEL` for Gemini or another
OpenAI-compatible API). The same Converse-shaped agent loop is translated automatically. `offline` needs no keys.

### Tests
```bash
pip install -r requirements-dev.txt && pytest -q
```

## Project layout
```
carecircle/server.py       MCP server (MCPServer, Streamable HTTP) + dashboard/simulator routes
carecircle/care.py         medication, wellbeing, appointment domain logic
carecircle/risk_agent.py   LangGraph Care Risk Agent
carecircle/safety.py       LLM-as-Judge safety layer
carecircle/insights.py     Bedrock vision (prescriptions) + weekly summary
carecircle/voice_agent.py  simulated Alexa+: real MCP client + Bedrock tool-use loop
carecircle/store.py        SQLite / DynamoDB storage
carecircle/notify.py       family alerts via Amazon SNS
web/                       alexa.html (voice simulator), dashboard.html
```

## Responsible AI
CareCircle is **not a medical device** and never gives medical advice. It never suggests changing medication, and
emergencies are always escalated to humans. Demo data is fictional.

## License
MIT
