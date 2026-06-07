---
name: warn-french-in-files
enabled: true
event: file
action: warn
conditions:
  - field: file_path
    operator: regex_match
    pattern: \.(md|py|ts|tsx|js|jsx|yml|yaml|json|toml)$
  - field: new_text
    operator: regex_match
    pattern: [àâçéèêëîïôûùüÿœÀÂÇÉÈÊ]
---

⚠️ **Possible French in a committed file.**
chrysa rule: all committed files (code, comments, docs, config) are **English-only**.
Accented characters were detected — if this is French prose, translate it.
(Legitimate i18n strings can be ignored.)
