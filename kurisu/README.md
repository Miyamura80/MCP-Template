# Kurisu

A LangChain agent that researches the latest development of OpenSSH through DeepWiki.
Every tool call goes through SealGate, in the KurisuLab org. The org admin controls it.

## Connect (once)

The admin must accept the KurisuLab org first.

```bash
source ../.venv/bin/activate
export SEALGATE_BASE_URL=https://self-host-ew-release-edison-watch-pr-1632.up.railway.app
sealgate connect --client-name "Kurisu"
```

Send the printed consent link to the admin. If the admin approves on another computer,
paste the full `http://127.0.0.1...` URL from their address bar into this terminal.
The tokens go to `.sealgate/agent-token.json`. Keep `.sealgate/` out of git and chat.

## Run

```bash
export OPENAI_BASE_URL=https://openrouter.ai/api/v1
export OPENAI_API_KEY=<your OpenRouter key>
export AGENT_MODEL=openai:z-ai/glm-5.3-flash
python agent.py "What changed recently in OpenSSH?"
```

`agent.py` needs neither `SEALGATE_BASE_URL` nor `SEALGATE_TOKEN`.

## Check

```bash
sealgate tools
sealgate call deepwiki_read_wiki_structure '{"repoName": "openssh/openssh-portable"}'
```
