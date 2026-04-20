# Thresholds — chrysa

> Source de vérité : `.claude/config.json` → `qualityThresholds`
> Lié aux règles Ruff (Python) / ESLint (TS) du projet.

## Python

| Règle | Valeur | Outil |
|-------|--------|-------|
| Max lignes / fonction | 40 | `ruff PLR0915` |
| Max lignes / fichier | 300 | `ruff` / `wc -l` |
| Max arguments / fonction | 5 | `ruff PLR0913` |
| Complexité cyclomatique | 10 | `ruff C901` |
| Warnings lint | 0 | `make ruff-check` |
| Coverage module modifié | ≥ 85% | `pytest --cov` |
| Docstring coverage | ≥ 80% | `interrogate` |

## TypeScript / React

| Règle | Valeur | Outil |
|-------|--------|-------|
| Max lignes / composant | 150 | ESLint |
| Max lignes / fonction | 50 | ESLint |
| Max lignes / fichier | 500 | ESLint |
| Max arguments / fonction | 4 | revue |
| Warnings lint | 0 | `npm run lint` |
| Erreurs tsc | 0 | `npm run type-check` |
| Coverage fichier modifié | ≥ 85% | `vitest --coverage` |

## Bundle / Perf (frontend)

| Règle | Valeur | Outil |
|-------|--------|-------|
| Bundle gzip | < 500 KB | Vite analyzer |
| Accessibility Lighthouse | ≥ 90 | Lighthouse CI |

## Vérification manuelle rapide

```bash
# Python
make ruff-check && make tests

# Node
make lint && make type-check && make test-coverage
```
