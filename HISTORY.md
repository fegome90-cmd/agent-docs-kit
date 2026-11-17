# Historial, hechos y Git · 0.3.0-rc.1

Esta aplicación es independiente de Trifecta. El historial conserva hechos y notas;
los verificadores aportan evidencia. Ninguno declara aprobación semántica.

## Instalar el único complemento del historial

Python >=3.11. Copia **todo** `tools/agent-docs-kit/`, incluidos módulos, plantillas,
licencias y hooks de ejemplo. En un entorno virtual propio:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r tools/agent-docs-kit/requirements-history.txt
.venv/bin/python tools/agent-docs-kit/agent_docs_kit.py --help
```

En Windows usa el ejecutable de `.venv/Scripts/`. Portalocker 4.4.2 sólo se importa
al escribir historial. Las lecturas, hechos y gate Git funcionan con biblioteca
estándar. La metadata PEP 723 del CLI también declara Portalocker para `uv run`.
Graphify sigue siendo un complemento externo opcional; no pertenece al núcleo.

## Una sesión, un archivo

Genera un UUID una vez, consérvalo durante esa sesión y abre otro para una nueva
sesión o rama. Ejemplo ilustrativo; sustituye el UUID por el tuyo:

```sh
python tools/agent-docs-kit/agent_docs_kit.py append . \
  --session 9b5b9e1c-03de-4d11-a536-dc6f93663da2 \
  --key revision-001 --summary "Revisé los comandos de instalación" \
  --file README.md --command "python -m unittest discover" --apply
python tools/agent-docs-kit/agent_docs_kit.py verify . --json
python tools/agent-docs-kit/agent_docs_kit.py render .
```

Se crea `docs/living/history/<UUID>.jsonl`. Los sidecars `*.jsonl.lock` coordinan
procesos; pueden excluirse de Git. No se borran locks para resolver contención.
El repositorio no recibe un `.gitignore` ni hooks automáticamente.

`--history` permite otra ruta relativa a ROOT. Cada evento contiene esquema, ID,
sesión, secuencia local, fecha UTC, tipo, origen declarado, clave de idempotencia
y payload. La hora no establece causalidad global entre sesiones.

Para reintentar la misma operación usa **la misma sesión, clave y contenido**. Se
devuelve el evento original y se sincroniza el archivo otra vez. Cambiar contenido
con la misma clave es un conflicto. Para corregir, agrega una entrada nueva;
puedes incluir `corrects` con el ID anterior en `--payload` (objeto JSON). Esa
referencia es informativa: esta versión no aplica automáticamente una corrección
ni resuelve contradicciones entre sesiones. `--payload` es alternativo a
`--summary`, `--file` y `--command`.

Los resúmenes se conservan dentro del registro. `--file` y `--command` son datos
declarados: no copian archivos ni ejecutan comandos. Para conservar un documento,
crea una revisión completa con nombre nuevo y referencia esa revisión.

```sh
python tools/agent-docs-kit/agent_docs_kit.py append . \
  --session 9b5b9e1c-03de-4d11-a536-dc6f93663da2 \
  --key cierre-001 --kind session.closed --summary "Terminado; quedan pendientes X e Y" --apply
python tools/agent-docs-kit/agent_docs_kit.py render . --output revision-001.md --apply
```

Un cierre lógico impide nuevas entradas, salvo reintentos idénticos de entradas
ya existentes. Un crash no equivale a cierre. Las exportaciones usan creación
exclusiva y su directorio padre debe existir. No hay `--force`.

Si una cola está incompleta o corrupta, `verify` y `append` fallan sin repararla
ni eliminar bytes. Una escritura fallida tiene resultado desconocido: reintenta
con la misma clave si el segmento sigue íntegro. Conserva el segmento dañado
para diagnóstico; la recuperación automática queda fuera de esta versión.

Los locks son cooperativos y locales. Flush/fsync se completan antes del éxito,
pero eso no certifica todas las combinaciones de sistema operativo, filesystem,
hardware o corte de energía. El límite es 1 MiB/evento y 64 MiB/segmento; inicia
otra sesión antes de agotarlo. No se hace rotación destructiva.

## Hechos declarados

`facts` usa un manifiesto JSON curado. Ejemplo incluido:

```sh
python tools/agent-docs-kit/agent_docs_kit.py facts . \
  --manifest tools/agent-docs-kit/examples/facts.example.json --json
```

Cada hecho declara `id`, `canonical` y `copies`; cada ubicación usa `file` y
`pattern`. El patrón debe tener exactamente un grupo de captura y una única
coincidencia en el archivo. Las capturas se comparan literalmente. Ambigüedad,
ausencia y discrepancia producen `review_required`, sin modificar documentos.
El manifiesto no puede comparar una ocurrencia consigo misma, aunque dos regex
distintas la capturen. Dos ocurrencias diferentes del mismo archivo sí son válidas.
El ejemplo comprueba la versión compartida por los dos módulos originales.

Las rutas son relativas a ROOT. No hay glob, YAML, búsqueda automática de hechos,
normalización numérica, red ni LLM. Los archivos son UTF-8 de hasta 1 MiB. Las
expresiones regulares son configuración confiable: el límite de archivo no es
un timeout del motor de regex. La cobertura es únicamente la declarada.

## Gate Git de sólo lectura

El snapshot, documentos, fuentes y manifiesto opcional se leen del **índice Git**.
Un archivo del working tree puede tener contenido diferente al preparado.
Esta versión rechaza conflictos de merge, enlaces simbólicos y submódulos en
cualquier ruta del índice, además de entradas `git add -N` sin contenido preparado.
No adoptes este gate sin ajustar ese alcance si tu proyecto necesita submódulos
o enlaces simbólicos versionados.

```sh
python tools/agent-docs-kit/agent_docs_kit.py snapshot . \
  --source src --document README.md \
  --output docs/living/snapshots/base.json --apply
git add src README.md docs/living/snapshots/base.json
python tools/agent-docs-kit/agent_docs_kit.py index-check . \
  --snapshot docs/living/snapshots/base.json --json
```

Para incluir hechos añade `--facts ruta/al/manifiesto.json`, también preparado.
El gate comprueba el prefijo de segmentos abiertos frente a HEAD, igualdad de
segmentos cerrados y nuevos segmentos válidos. Eliminar, renombrar o modificar
historia previa requiere revisión. No es una barrera contra quien evita el hook
o modifica directamente el repositorio. No hay instalación global ni Actions.

Los directorios se infieren de rutas versionadas: Git no representa directorios
vacíos. Un snapshot que registra uno puede pedir revisión aunque los archivos
coincidan. El snapshot debe quedar fuera de las fuentes observadas, y también
conviene separar el historial de los scopes para evitar autorreferencias.

El ejemplo `hooks/pre-commit.example` usa `AGENT_DOCS_KIT_PYTHON`,
`AGENT_DOCS_KIT_SNAPSHOT`, `AGENT_DOCS_KIT_HISTORY` y `AGENT_DOCS_KIT_FACTS`.
Estudia el hook que ya use tu repositorio y agrega una llamada; no lo reemplaces
sin conservar su comportamiento. El ejemplo no escribe, hace staging ni commits.
`GIT_INDEX_FILE` se admite para los índices temporales de commits parciales.

## Resultados y errores

- `0`: operación completada o comparación sin discrepancias dentro de su alcance.
- `1`: discrepancia/revisión requerida o destino de exportación ya existente.
- `2`: argumentos inválidos o falta de `--apply` para una escritura.
- `3`: error operativo, corrupción, esquema inválido o ruta insegura.

`append` emite el evento JSON. `facts` e `index-check` siempre emiten un reporte
JSON; `--json` también hace estructurados sus errores operativos. `verify --json`
devuelve el inventario validado. `render` imprime Markdown sin escribir por defecto.

## Compatibilidad con cobertura 0.4

El historial append-only no cambia. `index-check` añade `coverage_check` sólo
cuando el snapshot preparado usa esquema 2. Lee [COVERAGE.md](COVERAGE.md).
No mezclar un resultado limpio del índice con la cobertura de archivos untracked.

## Aceptaciones de conocimiento 0.5

`knowledge.accepted` usa el writer JSONL v1 existente. Conserva padres, propuesta,
documento, evidencia, review y baseline; selección por DAG, nunca por fecha.
Dos cabezas de ramas requieren resolución explícita de todas ellas. No borrar
eventos ni artefactos para resolver conflictos. El gate opt-in conserva los
prefijos de HEAD y además los artefactos aceptados de todos los ancestros.
Los límites y reintentos durables están en [KNOWLEDGE.md](KNOWLEDGE.md).
