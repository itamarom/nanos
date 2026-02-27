# Nanos

Personal nano-agents that run on the [Nanos Framework](https://github.com/itamarom/nanos).

Each subdirectory is a nano — a small Python script that automates a task through the gateway's API proxy.

## Structure

```
{nano-name}/
├── nano.py        # Script entry point
└── config.yaml    # Name, schedule, permissions
```

## Creating a nano

Open the Chat page in the dashboard and describe what you want to automate. The chatbot will generate the script, test it in draft mode, and register it — no manual setup needed.
