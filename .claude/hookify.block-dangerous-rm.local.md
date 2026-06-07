---
name: block-dangerous-rm
enabled: true
event: bash
pattern: rm\s+-rf
action: block
---

🚫 **Blocked: `rm -rf` detected.**
Recursive force-delete is blocked by chrysa safety policy.
Delete specific paths explicitly, or confirm the exact target with the user first.
