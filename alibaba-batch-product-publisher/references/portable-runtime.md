# Portable runtime

This skill is intentionally independent of any particular agent product. An agent needs:

- access to this skill folder and the user's publishing files;
- Python 3.10 or newer;
- permission to run local commands and make HTTPS requests;
- valid Alibaba Open Platform credentials and token;
- explicit user approval before image upload or product creation.

Install the one external Python dependency from the skill root:

```bash
python -m pip install -r requirements.txt
```

## Credential options

Preferred for automation: provide these process environment variables:

```text
ALIBABA_APP_KEY
ALIBABA_APP_SECRET
ALIBABA_TOKEN_FILE
ALIBABA_GATEWAY          optional
```

Alternatively, copy `assets/config.example.json` outside the skill folder, fill it, and
pass its path with `--config`. The legacy `--mcp-config` spelling remains supported.
The loader accepts:

1. the flat example JSON;
2. `{ "env": { ... } }`;
3. legacy agent MCP JSON containing `mcpServers.alibaba-api-tools.env`.

A relative token path is resolved against the config file's directory. The loader also
checks `ALIBABA_CONFIG_FILE`, `.alibaba-publisher.json` in the working directory,
`~/.config/alibaba-publisher/config.json`, and legacy WorkBuddy/Codex MCP locations.

Do not store a real config or token in the skill repository. Do not paste credentials into
an agent prompt. Use filesystem permissions suitable for secrets.

## First-run checks

```bash
# Parses config and token without network access
python scripts/doctor.py --config /secure/config.json --offline

# Calls only alibaba.icbu.product.list with page size 1
python scripts/doctor.py --config /secure/config.json
```

The doctor prints only status, a short AppKey fingerprint, token file path, request ID, and
safe error metadata. It never prints AppSecret or access token.

## Installing for another agent

Copy or link the complete `alibaba-batch-product-publisher` folder into that agent's skill
directory if it supports skills. Otherwise instruct the agent to read `SKILL.md` and run
the bundled commands from this folder. Do not copy only `SKILL.md`; the scripts,
references, template, and requirements are one versioned unit.

The agent should execute the programs, inspect their JSON summaries, and explain outcomes.
It should not rewrite the signing, XML, retry, or recovery logic.
