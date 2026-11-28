# AI assistants: your own Claude or ChatGPT, and the in-app assistant

There are two ways to ask questions about the app's data in plain language. Both use the same read-only tools, and both act as the person who is asking: same role, same account visibility, nothing written.

| | Your own Claude or ChatGPT | The in-app assistant (**Ask** in the menu) |
|---|---|---|
| Who pays for the model | The person's own Claude or ChatGPT subscription | The company key, under its own monthly allowance, or the person's own Anthropic key |
| Where you chat | claude.ai, Claude Desktop or mobile, Claude Code, ChatGPT | Inside the app |
| How it connects | Remote MCP server at `https://<app>/mcp`, OAuth sign-in through the app | Built in |

## Connecting your own Claude or ChatGPT

The address is shown under **My connections > Claude and ChatGPT**, with a copy button. In production it is `https://ri-api-oust.onrender.com/mcp`.

- **Claude** (claude.ai, Desktop, mobile): Settings > Connectors > Add custom connector, paste the address, Connect. On Team and Enterprise plans an owner adds the connector once, and then each person connects and signs in as themselves.
- **ChatGPT**: Plugins (or Settings > Apps) > Add custom MCP server, paste the address, choose OAuth. Some plans need Developer mode switched on first. Tools are marked read-only, so ChatGPT does not ask to confirm each call.
- **Claude Code**: `claude mcp add --transport http the-codest https://<app>/mcp`, then `/mcp` to sign in.

Signing in sends the person to the app's usual Google sign-in, then to a consent page (`/connect/authorize`). The page names the assistant, shows where the approval is sent, and says what the assistant can do. People can see and disconnect their connected assistants under My connections; access ends at once.

### How it is built (what Claude and ChatGPT require)

The app is its own OAuth 2.1 authorization server for the MCP server, following the [MCP authorization spec (2025-11-25)](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization) and the [Claude connector authentication requirements](https://claude.com/docs/connectors/building/authentication):

- `POST /mcp`: Streamable HTTP transport, stateless, with one JSON response per request. Without a valid token it answers `401` with `WWW-Authenticate: Bearer resource_metadata=".../.well-known/oauth-protected-resource/mcp", scope="ri.read"`.
- `/.well-known/oauth-protected-resource[/mcp]` (RFC 9728) and `/.well-known/oauth-authorization-server` (RFC 8414, PKCE `S256` advertised).
- `POST /oauth/register`: Dynamic Client Registration (RFC 7591). Claude and ChatGPT register themselves, so nobody copies client IDs. Redirect addresses are limited to `claude.ai`, `claude.com`, `chatgpt.com`, `chat.openai.com`, `platform.openai.com` and loopback addresses on any port (for Claude Code). Widen the list with `RI_MCP_REDIRECT_HOSTS`.
- `GET /oauth/authorize` checks the client and its exact redirect address, requires PKCE S256 and checks `resource` (RFC 8707), then hands over to the consent page.
- `POST /oauth/token` accepts form-encoded requests (or JSON) for `authorization_code` and `refresh_token`. Codes are single use and last 5 minutes; replaying one revokes the connection. Access tokens last 1 hour. Refresh tokens last 30 days (`RI_MCP_REFRESH_DAYS`) and are rotated on every use; presenting a spent one again, after a 30-second grace period, revokes the connection.
- `POST /oauth/revoke` (RFC 7009).
- Tokens are random strings stored only as SHA-256 hashes. They are bound to this server's `/mcp` address. App session tokens are not accepted at `/mcp`, and MCP tokens are not accepted anywhere else.
- Each request checks that the person is still active and still in the allowed email domain.

## The tools

`backend/src/ri/assistant/tools.py` defines the tools. They are built from the app's own routes:

- `search` and `fetch`, in the shape ChatGPT's company knowledge and deep research expect: `fetch` takes ids such as `account:<id>` or `person:<id>`.
- Named tools for the common questions: `whoami`, `today`, `today_brief`, `list_accounts`, `get_account` (with optional sections), `buying_signals`, `list_people`, `get_person`, `get_opportunity`, `open_tenders`, `findings`, `renewals`, `deadlines`, `forecast`, `competitors`, `competitor_deals`. Their parameters are taken from each endpoint's OpenAPI description, so they stay in step with the code.
- `list_endpoints` and `api_get` reach every other JSON `GET /api/...` endpoint the person may call, including new ones as soon as they are added. A short deny list leaves out files, raw webhook payloads and sign-in plumbing.

Each tool calls the app's own API in process with a short-lived internal token (`api/auth.py: issue_internal`). That token is signed with a key that exists only in the server's memory, and it opens **GET requests only**. Every permission check and row-scope rule therefore applies exactly as it does on the pages. Tools a role cannot use are not listed for that role. Results are cut to about 10,000 tokens, and the result says when it was cut. Every MCP tool call is recorded in `product_events` (`ai_tool_call`) with the assistant's name.

## The in-app assistant

- `POST /api/assistant/chat` streams newline-delimited JSON events: `tool` (what is being looked up), `answer`, `done`, `error`. The page keeps the conversation in the browser only.
- On the company key it uses `RI_ASSISTANT_MODEL` (default: the page-reading model, Claude Haiku). It has its own budget, `provider_budgets` `anthropic/assistant`, with a monthly cap of `RI_ASSISTANT_MONTHLY_TOKEN_CAP` tokens (default 3,000,000), so it can never use up the allowance for page reading and research. Usage appears in Administration > Usage as `assistant_chat`.
- A person can save their own Anthropic key under My connections (`PUT /api/me/ai-key`). The key is checked with Anthropic, sealed with `RI_VAULT_KEY` and bound to that person. With a personal key the assistant uses `RI_ASSISTANT_PERSONAL_MODEL` (default Claude Sonnet) and the company budget is not touched.
- At most 8 model calls per question, then it answers with what it has.

## Settings

| Variable | Default | What it does |
|---|---|---|
| `RI_PUBLIC_URL` | (required in production) | The issuer and the MCP address (`<url>/mcp`) |
| `ANTHROPIC_API_KEY` | none | The company key for the in-app assistant |
| `RI_ASSISTANT_MONTHLY_TOKEN_CAP` | 3000000 | The in-app assistant's monthly tokens on the company key (0 = off) |
| `RI_ASSISTANT_MODEL` | `RI_ANTHROPIC_MODEL` | Model for the company key |
| `RI_ASSISTANT_PERSONAL_MODEL` | claude-sonnet-5-5 | Model when a person uses their own key |
| `RI_VAULT_KEY` | none | Needed to save personal keys |
| `RI_MCP_REDIRECT_HOSTS` | claude.ai, claude.com, chatgpt.com, chat.openai.com, platform.openai.com | Hosts a sign-in may return to (`*` = any https host; not recommended) |
| `RI_MCP_REFRESH_DAYS` | 30 | How long a connected assistant stays signed in without being used |

## Not done (on purpose)

- **Writing through an assistant.** Every tool is read-only. Changes in this app go through proposals and approvals, and an assistant should use that same path. If writing is wanted, the next step would be a separate `ri.propose` scope whose tools only create proposals.
- **Client ID Metadata Documents.** These are not advertised, so the server never fetches addresses that clients supply. Claude and ChatGPT fall back to Dynamic Client Registration, which is supported.
- **OpenAI keys for the in-app assistant.** ChatGPT users connect through MCP instead.
