# Devpost submission text

**Project name:** CareCircle

**Elevator pitch (≤200 chars):** An Alexa+ MCP server that lets elderly parents living alone manage meds and wellbeing by voice, while an AI risk agent spots worrying patterns and keeps their family informed.

## Inspiration
Millions of older adults live alone, far from the children who worry about them. A daily phone call ("I'm fine!") can't reveal that Mum has skipped her evening tablet three times this week and has also felt dizzy twice. Voice is the most natural interface for older people, and Alexa+ now lets brands plug in through MCP. So I built the missing piece: a care companion that listens, notices patterns, and brings family in at the right moment.

## What it does
Rose, 74, just talks to Alexa:
- "What do I need to take today?" → today's plan.
- "I just took my blood pressure tablet" → the dose is logged; no drug names needed, because it fuzzy-matches by purpose.
- "I feel dizzy again" → logged and risk-assessed. Dizziness for the 3rd time this week, combined with missed doses, triggers a family alert with evidence.
- "Can I take two metformin tonight?" → the safety judge blocks dose advice and defers to her doctor or pharmacist.
- "I fell in the bathroom" → hard rule: an urgent family alert immediately, plus emergency guidance.
- "Tell my son to call me" → message delivered.

The family gets email/SMS alerts (Amazon SNS) and a live dashboard: adherence chart, mood and symptom timeline, alerts with the evidence behind them, upcoming appointments, an AI weekly summary, and prescription-photo import, where a vision model turns "BD / TDS / HS" into a schedule.

## How we built it
- **Self-hosted MCP server**, Python `mcp` 2.3 `MCPServer`, **spec 2025-11-25 over Streamable HTTP**. It has 11 tools with typed input schemas, structured output, tool annotations (`readOnlyHint`, `openWorldHint`), and server `instructions` tuned for voice.
- **Simulated Alexa+ client:** a web voice UI (Web Speech API) backed by a real MCP client (`ClientSession` + `streamable_http_client`) and an LLM tool-use loop. A live panel shows every `tools/call`.
- **Care Risk Agent (LangGraph):** `gather → detect → escalate | reason → act`. Red flags escalate by **deterministic rule, with no model in the loop**. Softer patterns (recurring symptom, missed doses in 48h, low-mood streak, symptom + missed meds) go to the LLM, which decides none / nudge / alert and writes a kind family message. Alerts are de-duplicated within 12h.
- **Safety judge (LLM-as-Judge):** every spoken reply goes through regex guards (no dose or diagnosis advice, emergency line added for red flags), then a judge scoring safety, groundedness in tool results, and clarity. Failing replies are rewritten.
- **AWS:** EC2 (Amazon Linux 2023, IAM role, systemd) behind **CloudFront** for HTTPS (origin locked to CloudFront's prefix list), **DynamoDB** single-table store, **SNS** family alerts, **S3 + SSM** for deploys, everything provisioned from **AWS CloudShell** with two scripts. The AI layer targets the **Amazon Bedrock Converse API** (tool use, vision, JSON judging).
- **Provider abstraction:** Bedrock → OpenAI-compatible fallback → rule-based offline mode, so the product works in any environment and safety never depends on a model call.

## Challenges we ran into
- Our AWS account returns *"Error 002: Access to Bedrock models is not allowed for this account"* for every model (even Amazon Nova), even after the Anthropic use-case form. We opened a support case and built the provider abstraction so the demo still runs end-to-end. The live demo runs the risk agent and judge in rule-based mode.
- There is no self-serve way to attach a personal MCP server to a real Alexa+ device, so we built a faithful simulator that consumes the server exactly as Alexa+ would: over MCP Streamable HTTP.
- The MCP Python SDK v2 renamed `FastMCP` → `MCPServer`, so most tutorials are outdated.
- Getting HTTPS without a domain: solved with CloudFront in front of EC2.

## Accomplishments that we're proud of
- Safety-first design: emergencies never depend on an LLM, and medication advice is always blocked.
- A real standards-compliant MCP server, deployed and live on AWS within the hackathon.
- Elderly-friendly UX: no drug names to remember, and replies are 1-3 short spoken sentences.

## What we learned
MCP is a natural fit for voice assistants: good tool descriptions and voice-oriented outputs matter as much as the code. Layering deterministic rules under LLM judgement gives you both safety and nuance.

## What's next for CareCircle
Real Alexa+ integration when available, proactive reminders (Alexa notifications), multiple caregivers with roles, doctor-visit summaries, multilingual support (Hindi, Telugu) for Indian households, and Bedrock once access is restored.

## Built with
python, mcp, model-context-protocol, alexa, langgraph, amazon-bedrock, amazon-ec2, amazon-cloudfront, amazon-dynamodb, amazon-sns, amazon-s3, aws-cloudshell, starlette, uvicorn, javascript, html, web-speech-api

## Links
- Repo: https://github.com/Sajidhussain19/carecircle
- Live demo: https://d6emajsgztu6y.cloudfront.net/alexa (dashboard: /dashboard, MCP: /mcp)

## Track & mini challenges
- Primary track: **Alexa+** (self-hosted MCP server, spec 2025-11-25, Streamable HTTP)
- Mini challenges: **AWS Builder** (EC2, CloudFront, DynamoDB, SNS, S3, SSM, IAM, CloudShell; Bedrock Converse integration)

**Built during the hackathon:** the entire project was created during the submission window.
