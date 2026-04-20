# ARCHITECTURE — {{REPO_NAME}}

> Vue architecturale statique · à mettre à jour avant tout PR impactant la structure

---

## Contexte système (C4 · niveau 1)

```mermaid
C4Context
  Person(user, "Utilisateur", "chrysa solo dev / client")
  System(this, "{{REPO_NAME}}", "{{ONE_LINER}}")
  System_Ext(notion, "Notion", "source vérité documentation")
  System_Ext(claudeapi, "Claude API", "agent IA")

  Rel(user, this, "utilise")
  Rel(this, notion, "lit/écrit via MCP")
  Rel(this, claudeapi, "appelle")
```

## Conteneurs (C4 niveau 2)

```mermaid
flowchart LR
  subgraph Frontend
    UI[React SPA · Vite]
  end
  subgraph Backend
    API[FastAPI]
    DB[(PostgreSQL 16)]
    REDIS[(Redis 7)]
  end
  UI -->|REST + WS| API
  API --> DB
  API --> REDIS
```

## Composants internes majeurs

| Composant | Rôle | Stack |
| --------- | ---- | ----- |
| | | |

## Flows critiques

### Flow 1 — {{nom}}

```mermaid
sequenceDiagram
  participant U as User
  participant A as API
  participant D as DB
  U->>A: request
  A->>D: query
  D-->>A: result
  A-->>U: response
```

## Modèle de données (principal)

Voir `docs/db-schema.dbml` ou Alembic migrations pour la source vérité.

## Intégrations externes

| Service | Direction | Protocole | Auth |
| ------- | --------- | --------- | ---- |
| | | | |

## Non-fonctionnels

- **Perf** : p95 < {{Xms}} sur endpoint critique
- **Scale** : {{N}} RPS cibles
- **Sécurité** : auth 4 modes (chrysa-lib) · secrets via Vault/env · CSP stricte
- **Observabilité** : Prometheus métriques + Loki logs + Sentry erreurs
- **Accessibilité** : WCAG 2.1 AA
- **i18n** : FR + EN dès V1

## Décisions architecturales

Voir `DECISIONS.md` (numérotation D-XXXX locale).

## Dépendances repos chrysa

```mermaid
graph LR
  this[{{REPO_NAME}}] --> chrysa-lib
  this --> shared-standards
```

## Dette technique connue

Voir `docs/TECH_DEBT.md` si présent, sinon section "🔴 À refactorer" du README.
