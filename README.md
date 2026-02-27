# Nanos

The goal of this project is to demonstrate an agent architecture with "good-enough" security for it to be personally usable.
We achieve this by keeping all APIs behind a specific API gateway, and requiring explicit (human) permission for sensitive API calls.

This project was built entirely with vibe-coding (zero lines written by a human). It is definitely not production grade, and is published as a POC for some security and product concepts. Hopefully someone will take this as inspiration for an actual product to build.

I do trust it enough to run my own stuff on it.

Scroll more for some demo clips.

## What's new about this approach?

It's been several years since LLMs became a thing, but unfortunately I haven't connected any of them to anything important because... It's really unsafe. All connectors I could find had both read and write permissions, and I couldn't see myself giving those permissions. But on the other hand, read only access only gets you so far.

With this architecture, you get to use AI for personal productivity. You can easily query and control anything that has an API, and each automation which is deemed useful can be vibe-coded to a more structured script in minutes.

## Why is this architecture safer to use?

Basically it's about eliminating the third component of the [lethal trifecta](https://simonwillison.net/2023/Jun/16/the-lethal-trifecta/).
Even more than preventing external communication, it's about preventing any action that has some external effect.

So, we connect the agent to everything: WhatsApp, Calendar, Email...
It has access to all data. It can send slack updates regularly (not considered external communication since it's only inside an organization).

But it can't delete calendar events. It can't send WhatsApp messages or emails. Well, actually, it can - the user just has to approve it.

<img width="2092" height="1046" alt="lethal-trifecta-fixed" src="https://github.com/user-attachments/assets/4fada6db-a625-468f-b926-5bf730cbb1cd" />

## Is this project actually secure?

No. This project was vibe-coded in one weekend, and did not go through any security scrutiny.
Definitely don't expose it to the internet. However, when run on a private machine, the chances for a breach are much lower.

---

## Architecture

- API keys are stored encrypted in the Postgres
- Encryption password is kept in memory in the API-gateway container
- Agents run in an isolated container with network only access to the gateway

This makes it non-trivial even for a malicious agent to get to the API keys or to make sensitive API calls without approval.

```
                    ┌──────────────┐
                    │  Slack Bot   │
                    │  (approvals) │
                    └──────┬───────┘
                           │
┌──────────┐    ┌──────────┴───────┐    ┌──────────────┐
│  Nanos   │───>│   API Gateway    │───>│ External APIs │
│ (worker) │    │   (FastAPI)      │    │ OpenAI, Gmail │
└──────────┘    │   :8000          │    │ Calendar,Slack│
                └──────────────────┘    │ WhatsApp,etc. │
┌──────────┐    ┌──────────────────┐    └──────────────┘
│  Celery  │    │    Dashboard     │
│  Beat    │    │   (FastAPI)      │
└──────────┘    │   :8001          │
                └──────────────────┘
┌──────────┐    ┌──────────────────┐
│  Redis   │    │   PostgreSQL 16  │
└──────────┘    └──────────────────┘
```

### Currently Supported APIs

| Category | Services |
|----------|----------|
| **AI** | OpenAI (chat, embeddings) |
| **Email & Calendar** | Gmail, Google Calendar |
| **Messaging** | Slack, WhatsApp |
| **Productivity** | Notion, Linear |
| **CRM** | HubSpot |

---

## Example workflows

### 1 - Chat will all of your data, use the AI to perform actions
https://github.com/user-attachments/assets/cbd2e024-7e3f-480e-9544-96662a5cc884

Since every sensitive action requires explicit permission, you can connect this to any data source or platform that supports an API (adding another API takes around 10 minutes with any vibe-coding tool).

### 2 - Chat → Creating Nano Script


https://github.com/user-attachments/assets/4e9302e2-381d-4b50-85ad-5b80c6b801f2



1. Open chat
2. Select which APIs to connect
3. Ask questions, query APIs
   - For example: *"List all HubSpot contacts that live in San Francisco."*
4. When ready, enter Nano Creation mode by hitting "Create Nano"
   - This injects a prompt which teaches the chat to compose nano agents
5. Describe the specific nano specs
6. The chat will now:
   - Compose a script
   - Run it in draft mode (all sensitive API request are just logged instead of actually asking for approval)
   - Iterate on changes until the script works as intended
   - Suggest saving it and running it regularly

### 3 - Agentic Nano
One of the example nanos is a simple agentic harness. When you create a nano of that type, you can choose which APIs it has access to and give it a prompt for a task.

Here are a couple of interesting agentic nanos to configure.

#### WhatsApp interaction alerts

**Permissions:** WhatsApp messages read, Slack alert send

**Prompt:**
> "I'm currently trying to schedule a meeting with the following contacts.
> Please go over these chats and send me a slack summary of any chat which I haven't replied to, or which haven't responded in 4 days and I should followup with. The contacts I'm currently trying to meet with:
> - Name & Phone number #1
> - Name & Phone number #2
> ...
> "

#### Email+Calendar alerts

**Permissions:**  Email read, Calendar read, Slack updates

**Prompt:**
Go over my emails from the last 72 hours.
Send me a slack summary of all threads which discuss a potential meeting which hasn't been scheduled yet.

Include both:
- Meetings which I suggested a time but didn't get a response
- Meetings which they suggested to which I didn't reply
- Meetings which we agreed on a specific time but no calendar invite was sent

---

## Quick start

```bash
git clone https://github.com/itamarom/nanos.git
cd nanos
rm -rf nanos
git clone https://github.com/itamarom/nanos-example.git nanos   # example nano scripts
cp .env.example .env          # set DB_PASSWORD and ADMIN_API_KEY
docker compose up -d
```

1. Open `http://localhost:8001` — you'll be prompted to set a **master password** (encrypts all API credentials at rest)
2. Go to **APIs** and add your credentials (OpenAI, Google, Slack, etc.)
3. Start chatting or register a nano

## Your nanos

Nano scripts live in a separate repo inside the `nanos/` directory (gitignored). The Quick Start above clones the example repo there. To use your own:

1. Fork or create your own nanos repo (see [nanos-example](https://github.com/itamarom/nanos-example) for a starting point)
2. Replace the `nanos/` directory:
   ```bash
   rm -rf nanos
   git clone https://github.com/your-org/your-nanos.git nanos
   ```

This keeps the framework and your nano scripts as separate repos — you can update either independently.

### Sandboxed execution

For Docker-sandboxed nano execution, set absolute host paths in `.env`:

```bash
NANO_HOST_NANOS_PATH=/absolute/path/to/nanos-framework/nanos
NANO_HOST_LOGS_PATH=/absolute/path/to/nanos-framework/.data/nano-logs
```

These are needed because the worker spawns sibling Docker containers that mount host directories.

## License

MIT
