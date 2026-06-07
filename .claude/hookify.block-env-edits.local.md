---
name: block-env-edits
enabled: true
event: file
action: block
conditions:
  - field: file_path
    operator: regex_match
    pattern: \.env(\.local|\.production)?$
---

🚫 **Blocked: editing a real .env file.**
Secrets must not be written by the agent. Edit `.env.example` / `.env.template`
instead, or set the value outside the repo.
