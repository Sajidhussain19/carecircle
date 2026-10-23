# Product feedback

## Model Context Protocol (Python SDK `mcp` 2.3, spec 2025-11-25, Streamable HTTP)
- **Used for:** the CareCircle server (`MCPServer`, 11 tools, Streamable HTTP at `/mcp`) and the simulated Alexa+ client (`ClientSession` + `streamable_http_client`).
- **Worked well:** decorator-based tools with automatic JSON schemas; structured output; tool annotations; `custom_route` let one process serve the MCP endpoint, dashboard and REST API. Protocol negotiation to 2025-11-25 worked first time, and in-process `Client(server)` made testing trivial.
- **Needs work:** the v1 → v2 rename (`FastMCP` → `MCPServer`) breaks almost every tutorial, including Alexa+ samples. `-> dict` fails structured output with an unclear error (`dict[str, Any]` works). The DNS-rebinding protection default needs a clearer note for servers behind CloudFront or a load balancer.
- **Onboarding:** good once you read the SDK source; the docs lag the release. **Would build with it again:** yes.

## Alexa+ (MCP integration)
- **Used for:** the target platform. The server follows Alexa+ guidance: concise tool descriptions, server `instructions`, voice-friendly outputs (`say_to_user`), and annotations.
- **Needs work:** there is no self-serve way for an individual developer to connect a personal MCP server to a real Alexa+ device or a test console, so we had to build a simulator. A developer console like the old Alexa Skills Kit simulator, aimed at MCP servers, would be the single most valuable addition.
- **Would build with it again:** yes, the MCP model fits voice assistants well.

## AWS services
- **Amazon EC2:** hosts the MCP server (Amazon Linux 2023, systemd, IAM instance role, IMDSv2). Easy to use; user-data plus SSM `send-command` makes redeploys one command.
- **Amazon CloudFront:** HTTPS in front of EC2 with the managed `CachingDisabled` and `AllViewerExceptHostHeader` policies. SSE/streamable HTTP passes through fine. The origin is locked to CloudFront's origin-facing prefix list. This is a great "HTTPS without a domain" option for hackathons.
- **Amazon DynamoDB:** single-table store (`pk = user#kind`, `sk = ts#id`), on-demand billing. No issues.
- **Amazon SNS:** family alerts by email (SMS-ready). It worked first time; the email confirmation step is a necessary manual click.
- **AWS CloudShell:** used for all provisioning and deployment (Python 3.13 and Docker preinstalled). It was excellent and removed the need for any local setup.
- **Amazon S3 / IAM / SSM:** code bundle delivery, least-privilege instance role, remote updates.
- **Amazon Bedrock (Converse API):** the AI layer is written against Converse (tool use, vision, JSON judging). **Blocker:** our personal account gets `Error 002: Access to Bedrock models is not allowed for this account` on *every* model, including Amazon Nova, even after the Anthropic use-case form was "submitted successfully". There is no explanation and no self-serve fix. We built a provider abstraction (Bedrock first, then an OpenAI-compatible fallback, then a rule-based offline mode) so the product still works. Suggestion: show the account-level block, its reason and the fix inside the Bedrock console.

## LangGraph
- **Used for:** the Care Risk Agent state machine (`gather → detect → escalate | reason → act`) with conditional routing. Clear and testable. Would use again.
