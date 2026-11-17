# Kit documental para agentes

**Versión 0.5.0-rc.1 — candidato local para revisión.** Conserva el bootstrap y snapshot/status; añade historial append-only, hechos documentales y un gate del índice Git. El agente investiga y redacta; el script no deduce aprobación ni ejecuta el proyecto. Copia el directorio completo con todos los módulos, `templates/`, licencias y ejemplos.

Para descubrir archivos fuera de cobertura lee [COVERAGE.md](COVERAGE.md).

Empieza por [HISTORY.md](HISTORY.md) para `append`, `verify`, `render`, `facts` e `index-check`. La [selección y procedencia](REUSE.md) identifica el código adaptado y los componentes que se dejaron fuera.

Para mantener documentación existente, empieza por [LIVING_DOCS.md](LIVING_DOCS.md): `snapshot` y `status` funcionan con los documentos que elijas, sin crear ni reemplazar AGENTS.md. No certifican el contenido ni lo actualizan automáticamente.

## Uso rápido

Requiere Python >= 3.11; `uv` es opcional. Puede invocarse directamente con `python /ruta/al/kit/agent_docs_kit.py --help`. La escritura de historial requiere Portalocker 4.4.2, declarado en PEP 723 y `requirements-history.txt`. Los demás comandos del núcleo usan biblioteca estándar. No requiere Poetry ni bootstrap Bash. `uv` puede obtener un intérprete compatible en su almacenamiento gestionado. Para impedir descargas de Python y usar solo un intérprete ya disponible:

```sh
uv run --no-python-downloads --script /ruta/al/kit/agent_docs_kit.py inspect /ruta/al/repositorio
```

Si falta un intérprete compatible, esta variante se detiene; no instala uno. La opción controla descargas de Python, no es un modo sin red general.

Usa rutas explícitas. `ROOT` debe ser un directorio existente sin componentes simbólicos.

```sh
uv run --script /ruta/al/kit/agent_docs_kit.py inspect /ruta/al/repositorio
```

Este comando solo lee. Un resultado sin conflictos significa que los cinco destinos están libres, no que falten autoridades documentales en otras rutas. Antes de escribir, sigue [AUTHORING.md](AUTHORING.md) y acuerda con el usuario la relación con las instrucciones y documentos existentes.

Con esa revisión y la creación dentro del repositorio aprobadas:

```sh
uv run --script /ruta/al/kit/agent_docs_kit.py prepare /ruta/al/repositorio --apply --authorities-reviewed
```

Para redactar una propuesta sin modificar el repositorio:

```sh
uv run --script /ruta/al/kit/agent_docs_kit.py prepare /ruta/al/repositorio --output /ruta/a/propuesta --apply
```

`OUTPUT` puede no existir. Debe estar fuera de `ROOT` y no contenerlo. Esta modalidad no requiere `--authorities-reviewed`: no activa documentos dentro del repositorio ni autoriza integrarlos después. Los destinos de la propuesta también deben estar libres.

Tras redactar y revisar los documentos:

```sh
uv run --script /ruta/al/kit/agent_docs_kit.py check /ruta/al/repositorio
```

Para una propuesta externa, usa su directorio como raíz de `check`. Las referencias a archivos del repositorio que no estén en la propuesta pueden seguir dando hallazgos; registra esa limitación, no copies archivos solo para silenciarlos.

## Grafo opcional con Graphify

`graph` ejecuta una herramienta local externa para orientar la lectura del código; no crea los cinco borradores ni ejecuta comandos del proyecto destino. Requiere autorización específica mediante `--apply` y Graphify **0.9.26** ya instalado y disponible como `graphify`. El kit consulta `graphify --version` y se detiene si falta o difiere; no instala herramientas. Solo si falta, esta es la instrucción de instalación externa para una tarea separada y autorizada:

```sh
uv tool install graphifyy==0.9.26
```

Graphify y sus dependencias pertenecen a esa instalación externa; Portalocker corresponde únicamente al historial del kit. Revisa primero el corpus elegido, su sensibilidad y las exclusiones; `.gitignore` y `.graphifyignore` no sustituyen esa revisión. Con la ejecución y su ubicación aprobadas:

```sh
uv run --script /ruta/al/kit/agent_docs_kit.py graph /ruta/al/corpus --output /ruta/a/grafo-nuevo --apply --exclude 'private/**' --exclude 'generated/**'
```

En el corpus, Graphify puede indexar enlaces simbólicos a archivos internos; revísalos y exclúyelos con `--exclude` si no deben formar parte del grafo. La validación del kit rechaza componentes simbólicos en ROOT y OUTPUT, no filtra todo el contenido interno.

La interfaz es `graph ROOT --output OUTPUT --apply [--exclude PATTERN ...]`. `--exclude` es repetible y añade patrones a las exclusiones de Graphify. `ROOT` debe existir. `OUTPUT` debe ser nuevo, externo a `ROOT` y no contenerlo; se rechazan enlaces simbólicos en esas rutas. No se reutiliza ni sobrescribe una salida anterior: para actualizar un grafo, ejecuta el comando con otro `OUTPUT` nuevo.

`--timeout SEGUNDOS` limita la extracción a 120 segundos por defecto. El valor debe ser positivo y finito; la comprobación de versión tiene como máximo 10 segundos. Un timeout termina con código 3 y, si la carpeta ya existe y sigue siendo escribible, conserva el log parcial y `run.json` fallido. El ejecutable localizado se convierte en ruta absoluta antes de cambiar el directorio de trabajo. Se registran hora de inicio/fin y límite temporal. Este límite no es un sandbox ni un límite de memoria: stdout/stderr aún se capturan en memoria durante el proceso.

El kit invoca `extract` con `--code-only --no-cluster --max-workers 1`, sin flags arbitrarios. Limpia las variables heredadas `GRAPHIFY_*`, fija `GRAPHIFY_OUT=graphify-out` y desactiva la escritura de bytecode Python. No instala hooks ni instrucciones para agentes; no usa LLM, MCP, clustering ni genera HTML. Estas opciones deshabilitan el flujo LLM, pero no son un sandbox absoluto.

La salida contiene `graphify-out/graph.json`, caché y manifiesto derivados de Graphify, además de `graphify.log` con stdout/stderr. El kit agrega transiciones a `run.jsonl` y crea `run.json` una sola vez como resumen final `complete` o `failed`. Si la extracción o validación falla después de crear la salida, la carpeta y los logs se conservan para inspección; los errores previos, como herramienta ausente o versión distinta, no crean la salida. Se exige un JSON con listas `nodes` y `edges` y nodos no vacíos; esto no certifica sus relaciones. Las cachés propias de Graphify siguen su contrato externo y no son el historial autoritativo del kit.

Lee `graph.json` con las herramientas de lectura del agente. Usa sus rutas y referencias de código para volver a las fuentes: el grafo no es autoridad documental ni un callgraph perfecto. Registra corpus, exclusiones, versión y momento de extracción; puede quedar obsoleto en cuanto cambie el código. El flujo de contraste está en [AUTHORING.md](AUTHORING.md#3-investigar-sin-ejecutar-el-repositorio).

## Documentos creados

| Destino | Contenido propio |
|---|---|
| `AGENTS.md` | Entrada breve, autoridades, lectura por intención y reglas críticas verificadas. |
| `docs/agent/CONTEXT.md` | Propósito, actores, conceptos, alcance y límites del producto. |
| `docs/agent/ARCHITECTURE.md` | Composición, responsabilidades, dependencias e invariantes. |
| `docs/agent/CODEMAP.md` | Entradas y superficies de cambio; mapa superficial. |
| `docs/agent/RUNTIME.md` | Entorno, configuración, ejecución y verificación. |

Las plantillas bajo `templates/` conservan estas rutas. Se copian sin sustituir preguntas por datos inferidos. Contienen el marcador `<!-- agent-docs-kit:draft -->` y placeholders `{{...}}`; la creación no deja documentación terminada. `README.md` y `AUTHORING.md` pertenecen al kit y no se copian al destino.

## Escritura y conflictos

- `inspect` y `check` son de solo lectura. `prepare` requiere `--apply`; sin `--output`, también requiere `--authorities-reviewed`. `graph` requiere `--apply` y un `--output` nuevo y externo.
- Cualquier objeto que ocupe uno de los cinco destinos bloquea todo el conjunto: archivo, directorio o enlace, incluso roto. No hay fusión ni `--force`.
- Se rechazan enlaces simbólicos en componentes de las raíces y destinos. Las rutas se validan antes de resolver enlaces; las lecturas documentales y los enlaces locales tampoco los siguen.
- En `prepare`, los archivos se crean de forma exclusiva. Ante un fallo se intenta retirar solo los archivos creados por esa ejecución y los directorios nuevos que sigan vacíos. Se informa una limpieza incompleta. `graph` conserva su salida fallida y los logs.
- No se garantiza una transacción atómica entre cinco archivos ni protección ante un atacante que cambie rutas durante la operación. Trabaja en un árbol sin modificaciones concurrentes.

## Qué verifica `check`

Comprueba presencia y lectura de los cinco documentos, texto UTF-8 con saltos LF y ausencia de borradores pendientes. Revisa enlaces Markdown inline locales simples, como `[texto](ruta)` y `[texto](<ruta con espacios>)`, relativos al documento. No sigue enlaces fuera de la raíz ni enlaces simbólicos. No hace peticiones de red.

Las anclas `#seccion`, los enlaces de referencia y la sintaxis Markdown compleja requieren revisión manual. En un enlace con ruta y ancla, comprueba la ruta, no la existencia de la sección. Un resultado sin hallazgos **no certifica semántica**, vigencia de comandos, autoridades correctas ni coherencia entre documentos y código.

## Códigos de salida

| Código | Significado |
|---|---|
| `0` | Operación completada o comparación sin discrepancias dentro de su alcance; incluye historial, facts e index-check. |
| `1` | Conflictos al inspeccionar/preparar, hallazgos de check, destino de salida ya existente o comparación `review_required`. |
| `2` | Uso inválido de argumentos o falta de flags de autorización. |
| `3` | Error de E/S, raíz/estructura insegura, snapshot inválido o fallo de Graphify, su versión, límite temporal o validación del grafo. |

La ayuda de `--help` también termina con `0`. La autorización para preparar borradores no autoriza ejecutar comandos ni instalar dependencias del repositorio documentado. La invocación local de Graphify necesita su propia autorización; tampoco autoriza ejecutar ese proyecto.

## API local

El script expone `DOCUMENTS: tuple[Path, ...]` y estas funciones:

| Firma | Resultado |
|---|---|
| `inspect(root: Path) -> tuple[str, ...]` | Conflictos; no escribe. |
| `prepare(root: Path, output: Path | None = None) -> None` | Escribe borradores; el llamador debe gestionar la autorización. |
| `check(root: Path) -> tuple[str, ...]` | Hallazgos estructurales; no escribe. |
| `graph(root: Path, output: Path, excludes: Sequence[str] = ()) -> Path` | Ejecuta Graphify y devuelve la ruta de `graph.json`; el llamador debe gestionar la autorización. |
| `main(argv: Sequence[str] | None = None) -> int` | Interfaz de comandos y códigos de salida. |

La API `graph` admite además `timeout: float = 120.0` como argumento keyword-only. La API usa `ValueError` para raíz o estructura insegura, `FileExistsError` para conflictos y `OSError` para errores de E/S. Los defectos de programación no se ocultan como resultados satisfactorios. La guía de investigación y aceptación está en [AUTHORING.md](AUTHORING.md). La API de `living_docs.py` se documenta en [LIVING_DOCS.md](LIVING_DOCS.md).

## Verificar el kit

Desde la raíz del repositorio que contiene `tools/agent-docs-kit/`, usando el entorno donde instalaste `requirements-history.txt`:

```sh
.venv/bin/python -B -m unittest discover -s tools/agent-docs-kit/tests -v
```

La integración real de Graphify se omite por defecto. Para incluirla, instala
`graphifyy==0.9.26` en ese entorno y ejecuta:

```sh
KIT_E2E_GRAPHIFY=1 .venv/bin/python -B -m unittest discover -s tools/agent-docs-kit/tests -v
```

Las pruebas usan `unittest` y repositorios temporales. La suite incluye 14 recorridos
e2e con procesos reales; las pruebas unitarias también usan dobles para inyectar
fallos de E/S. Un `skip` por el filesystem o por Graphify omitido no equivale a un
caso aprobado. La evidencia de la distribución registra el entorno ejecutado.

Para lint y tipos, estos comandos usan Ruff y mypy del entorno de desarrollo existente; no son dependencias de ejecución del kit ni instalan nada en el repositorio documentado:

```sh
uv run --no-sync --no-python-downloads ruff check tools/agent-docs-kit
uv run --no-sync --no-python-downloads mypy --strict tools/agent-docs-kit/agent_docs_kit.py
```

Si esas herramientas no están disponibles, registra la verificación pendiente; instalarlas es una tarea separada. Estos comandos están documentados para ejecutarse, no constituyen por sí solos evidencia de que se hayan ejecutado o pasado.

## Continuidad documental opt-in · 0.5

El namespace `knowledge` conecta un mapa curado con secciones, conserva propuestas
y revisiones declaradas y permite leer la revisión aceptada desde otro proceso.
Consulta [KNOWLEDGE.md](KNOWLEDGE.md) para contratos, límites y códigos; los
[ejemplos](examples/knowledge/README.md) muestran los formatos. `status` e
`index-check` sólo evalúan estas obligaciones con `--knowledge-map MAP`. Sin ese
flag conservan sus contratos anteriores. La identidad del revisor es declarada,
no autenticada; `semantic_review` permanece `not_attested`. No hay watcher, DB
ni cliente LLM. Piloto, distribución y estudio de utilidad se cuentan por separado.
