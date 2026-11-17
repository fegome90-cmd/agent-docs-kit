# Deuda tecnica conocida

Medido el 2026-11-17 contra v0.5.0-rc.1. Este repo es un **WIP**, no un release.

## Estado de las herramientas

| Herramienta | Comando | Estado | Bloqueante en CI |
|---|---|---|---|
| ruff (lint) | `uv run ruff check .` | **0 errores** | Si |
| ruff (format) | `uv run ruff format --check .` | **67 archivos formateados** | Si |
| mypy (strict) | `uv run mypy .` | **1124 errores** en 40 archivos | No |
| pyrefly | `uv run pyrefly check` | **158 errores** | No |
| pytest | `uv run pytest` | **5 fallos, 363 pasan** | No |

## Por que mypy y pyrefly no bloquean

El 82% de los errores de mypy son `no-untyped-call` (616) y `no-untyped-def` (308): el codigo
heredado simplemente no tiene anotaciones. Con la config relajada baja a **181 errores**, o sea
que el deficit real es de anotacion, no de tipos mal escritos.

El objetivo es `strict = true` con 0 errores. Cuando llegue, se quita `continue-on-error`
del job `types`.

## Por que pytest no bloquea

Dos causas distintas, ambas verificadas:

1. **5 fallos preexistentes del candidato.** Corriendo el candidato pristino
   (`agent-docs-kit-build-v0.5.0-rc.1-egm/candidate/`, sin mis cambios) dan los mismos 5:
   - `test_fixture_helpers_strip_ambient_git_redirects`
   - `test_index_history_root_file_is_operational_error_in_both_commands`
   - `test_status_caps_document_on_every_preliminary_read`
   - `test_status_caps_new_directory_source_on_first_read`
   - `test_status_caps_snapshot_on_first_read`

2. **Contaminacion de git ambiente.** Cuando la suite corre dentro de un work tree git,
   los tests que crean repos temporales heredan el entorno y fallan con
   `modo 160000` (gitlink). En un directorio sin `.git`, esos tests pasan.
   Por eso el job de CI extrae el arbol a `$RUNNER_TEMP` antes de correr pytest.

## Findings del re-audit

El re-audit final deja **3 findings Major abiertos** y no establece convergencia EGM formal.
Ver `reviews/reaudit-final-round2/`.

## Plan de pago

1. Extraer el helper de aislamiento de git a un fixture compartido; usarlo en todos los tests
   que crean repos. Eso elimina la causa 2.
2. Arreglar los 5 tests preexistentes (causa 1).
3. Anotar los modulos de a uno, en orden de uso: `agent_docs_kit.py`, `history.py`,
   `living_docs.py`, `git_gate.py`, `doc_*.py`. Cada modulo anotado baja el contador.
4. Cerrar los 3 findings Major.
5. Cuando mypy strict = 0 y pytest = 0 fallos, quitar los `continue-on-error`.
