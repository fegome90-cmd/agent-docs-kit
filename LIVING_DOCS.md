# Mantener documentación vinculada a evidencia

`snapshot` conserva una referencia de los bytes observados. `status` compara el árbol actual con esa referencia. Cuando algo cambia, el agente o la persona revisa si hace falta actualizar la explicación. El programa no redacta, no ejecuta el repositorio y no acredita una revisión semántica.

## Inicio con documentación existente

Elige las fuentes de las afirmaciones documentales: código relevante, pruebas, manifiestos, configuración y decisiones. Puedes observar cualquier documento; este flujo no exige los cinco archivos del bootstrap.

```sh
python /ruta/kit/agent_docs_kit.py snapshot /ruta/proyecto \
  --source src --source tests --source package.json \
  --document README.md --document docs/architecture.md \
  --output /ruta/proyecto/docs/evidence-v001.json --apply

python /ruta/kit/agent_docs_kit.py status /ruta/proyecto \
  --snapshot /ruta/proyecto/docs/evidence-v001.json --json
```

Estos comandos son ejemplos: usa rutas que existan en tu proyecto. `--source` y `--document` son relativos a ROOT y se pueden repetir. `--output` y `--snapshot` son relativos al directorio desde el que invocas el comando si no usas una ruta absoluta. Se exige al menos una fuente y un documento.

La salida de snapshot es un JSON nuevo. Si ya existe, el comando falla con código 1 y conserva sus bytes. El snapshot debe estar fuera de las fuentes y documentos observados. Si observas todo el proyecto con `--source .`, guarda el snapshot fuera de ROOT. La misma regla se comprueba al leer una referencia trasladada.

Trabaja sobre un árbol quieto. La lectura no es una transacción atómica; el programa detecta errores de lectura y ciertas inconsistencias, pero no garantiza una fotografía coherente ante ediciones simultáneas.

## Interpretar la respuesta

| Resultado | Significado | Acción |
|---|---|---|
| `unchanged_since_snapshot`, salida 0 | Las rutas y bytes observados coinciden con la referencia. | Puede usarse esa referencia sin atribuirle revisión ni verdad adicionales. |
| `review_required`, salida 1 | Cambió una fuente, documento, directorio o tipo de alcance. | Revisar el diff y el documento propietario. |
| `error`, salida 3 | No se pudo establecer una comparación válida. | Resolver el error; no interpretar como ausencia de cambios. |
| Error de argumentos, salida 2 | La invocación no cumple la sintaxis. | Corregir los argumentos según `--help`. |

Los errores operativos de `status --json` son JSON en stdout. Los errores de argumentos usan el comportamiento normal de argparse: texto en stderr y código 2.

El resultado separa fuentes y documentos, cada uno con listas `added`, `modified` y `deleted`. Un renombre aparece como baja y alta. `scope_changes` informa desapariciones o cambios archivo/directorio del alcance; `directory_changes` incluye cambios de directorios vacíos. Los documentos son una selección explícita: un documento nuevo no seleccionado queda fuera de cobertura hasta que lo incluyas en una captura posterior.

La comparación usa contenido, no solo fecha ni HEAD. Modificar un archivo sin commit exige revisión; cambiar únicamente su fecha no. Un commit nuevo que no cambie los bytes observados no basta para exigir revisión. Git HEAD se conserva como contexto opcional; una carpeta sin Git también funciona.

Todos los resultados mantienen `semantic_review: not_attested`. Volver a capturar las fuentes después de cambiarlas no demuestra que alguien haya leído o corregido la documentación. La aceptación se registra mediante tu revisión del patch o PR habitual.

## Qué entra en la captura

Un archivo explícito observa ese archivo. Una carpeta observa sus archivos y subcarpetas, incluidos nombres nuevos al volver a ejecutar status. Si un archivo seleccionado se convierte en carpeta, el programa informa el cambio de tipo; no amplía silenciosamente la selección.

La política de exclusión es fija y queda guardada en el JSON: `.git`, `.hg`, `.svn`, `.venv`, `node_modules` y `__pycache__`. Una fuente o documento seleccionado explícitamente bajo esos componentes se rechaza antes de escribir. No lee `.gitignore` ni interpreta reglas de herramientas de terceros. Tampoco detecta secretos. Elegir un directorio amplio implica observar todos sus archivos no excluidos, aunque estén ignorados por Git. El JSON contiene rutas, tamaños y hashes; no copia el contenido.

Se rechazan rutas absolutas o con `..` en las selecciones, symlinks y objetos que no sean archivos/directorios normales. Un enlace simbólico o fallo de permisos dentro de un alcance es error; no se omite silenciosamente. No se promete protección ante sustituciones concurrentes maliciosas ni autenticidad del JSON.

La relación entre fuentes y documentos es deliberadamente conservadora: todos los documentos seleccionados dependen del conjunto de fuentes. Un cambio de comentario también requiere revisión porque cambian los bytes. No se calcula impacto por símbolo ni se afirma que la prosa haya quedado falsa. Si el ruido de revisión es excesivo, usa alcances menores o capturas separadas por componente.

## Flujo de mantenimiento

1. Revisa los documentos, sus autoridades y el alcance de evidencia. Captura la referencia tras esa revisión y conserva la decisión en el mecanismo habitual del proyecto.
2. Al cambiar código, pruebas, comandos, configuración o decisiones, ejecuta status. Hazlo antes de dar por terminado el cambio; no hace falta un watcher permanente.
3. Lee los cambios y decide qué afirmaciones necesitan edición. Si el cambio no afecta la explicación, registra brevemente el motivo en la revisión.
4. Edita el documento propietario. Los otros documentos enlazan a él para evitar contratos duplicados.
5. Comprueba estructura y referencias. El `check` heredado sigue comprobando sus cinco documentos estándar; para otra estructura usa el validador existente del proyecto. Ejecuta las pruebas pertinentes cuando estén dentro del alcance autorizado.
6. Para un cambio contractual importante, pide lectura independiente con fuentes y contexto nuevo. El lector debe poder explicar la decisión, localizar evidencia y señalar contradicciones.
7. Acepta el cambio documental junto al cambio de código. Genera una referencia nueva con nombre versionado y actualiza la referencia utilizada por tu proceso. Git conserva las versiones anteriores.

No copies automáticamente los cinco documentos en cada repositorio. Si README y documentación existente ya cubren un rol, úsalos. Un AGENTS.md breve puede dirigir la lectura; no necesita contener toda la arquitectura.

## Tres controles distintos

| Dimensión | Control | Límite |
|---|---|---|
| Estructura | `check` para los cinco documentos estándar, o el validador del proyecto | No comprueba exactitud del contenido. |
| Evidencia observada | `status` | No evalúa el significado ni el contenido fuera de los alcances. |
| Corrección y aceptación | Lectura de fuentes, pruebas relevantes y revisión del patch | Requiere juicio; un snapshot no lo reemplaza. |

Los placeholders y el marcador draft indican contenido esencial incompleto. Una discrepancia conocida necesita explicación de su efecto y decisión; no se elimina para obtener un estado verde. La aprobación se mantiene en el proceso de revisión y no se simula mediante un campo editable.

## API local

```python
snapshot(root: Path, sources: Sequence[str], documents: Sequence[str], output: Path,
         *, discover_roots: Sequence[str] | None = None) -> dict
status(root: Path, snapshot_path: Path) -> dict
```

`snapshot` escribe exclusivamente la referencia nueva; la API llamadora gestiona autorización. `status` no modifica archivos. La CLI evita generar bytecode al importar este módulo. Las excepciones operativas son `ValueError`, `FileExistsError` y `OSError`; la CLI las traduce a los códigos descritos.

El formato persistido tiene `schema: agent-docs-kit/living-docs`, `schema_version: 1`, versión del kit, fecha UTC, algoritmo SHA-256, política de exclusión, scopes tipados, directorios observados, registros de fuentes/documentos, HEAD opcional y `coverage: declared_sources_and_documents`. La compatibilidad la gobierna el esquema y la política; no se exige que la captura provenga de la misma release del kit. El esquema se valida antes de recorrer las rutas de la captura. Versiones de esquema desconocidas o entradas incoherentes se rechazan. La especificación completa está en `docs/designs/living-docs-v0.2.md` dentro de la distribución.

## Alcance de esta versión candidata

La versión permite probar detección de cambios y mantenimiento asistido. No tiene generación automática de prosa, planificador, servidor, MCP, LLM integrado ni integración con un proveedor Git. Graphify sigue siendo opcional y no interviene en snapshot/status. La compatibilidad y el valor en cada proyecto se confirman mediante un piloto; consulta el informe de validación de esta entrega para los resultados efectivamente ejecutados.

## Descubrimiento opcional en 0.4

`--discover-root` produce esquema 2 y activa cobertura Git persistida.
Sin esa bandera se conserva el esquema 1 y su API anterior.
Ver [COVERAGE.md](COVERAGE.md) para límites, errores y diferencias entre working tree e índice.

## Integración opcional 0.5

`status ROOT --snapshot PATH --knowledge-map MAP --json` añade obligaciones
documentales sin borrar diferencias de bytes/cobertura. PATH mantiene resolución
relativa al cwd; con el opt-in debe quedar dentro de ROOT, o devuelve código 3
`unsupported_external_snapshot`. Sin flag se permiten las rutas heredadas.
`index-check ROOT --snapshot REL --knowledge-map MAP --json` evalúa sólo blobs
preparados, sin fallback local. Mapas, revisiones, reviews y eventos nuevos deben
prepararse explícitamente. No instala hooks. [Detalles](KNOWLEDGE.md).
