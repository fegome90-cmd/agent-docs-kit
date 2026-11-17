# Cobertura documental con Git · 0.4.0-rc.1

Esta versión detecta rutas elegibles que faltan en la evidencia documental.
No redacta fichas técnicas, no interpreta impacto semántico ni acredita que la
prosa esté vigente. `semantic_review` siempre sigue siendo `not_attested`.

## Crear una referencia con descubrimiento

Desde la raíz del repositorio, con un Python del kit:

```sh
python /ruta/agent-docs-kit/agent_docs_kit.py snapshot . \
  --source src/main.py --source package.json --document README.md \
  --discover-root src --output docs/living/snapshots/base-v2.json --apply
python /ruta/agent-docs-kit/agent_docs_kit.py status . \
  --snapshot docs/living/snapshots/base-v2.json --json
```

Usa únicamente fuentes y documentos que existan. Los roots son directorios
relativos a ROOT y se repite `--discover-root` para declarar varios. No se admiten
roots solapados, duplicados ni componentes simbólicos. Deben usar exactamente la
grafía del filesystem, incluso en volúmenes que no distinguen mayúsculas.
El snapshot debe quedar fuera de los roots de descubrimiento y de las fuentes.

`--discover-root` NO incorpora automáticamente los archivos descubiertos. La
captura puede crearse con cobertura incompleta: ejecutar `status` después revela
esa condición. Volver a capturar sin ampliar las fuentes tampoco la resuelve.
Para adoptar un archivo, revisa su contenido y papel documental, declara su ruta
como fuente y crea un snapshot nuevo. El anterior no se reemplaza.

## Esquemas compatibles

Sin `--discover-root`, `snapshot` sigue creando esquema 1. Sus lecturas y estados
mantienen el contrato anterior, también sin Git. No aparece `coverage_check`.
Con la bandera, se crea esquema 2 con este campo adicional:

```json
{"discovery": {"mode": "git", "roots": ["src"]}}
```

El campo queda guardado; `status` e `index-check` no requieren otra bandera.
La release del kit no invalida snapshots del mismo esquema. Esquemas desconocidos,
claves adicionales, roots no canónicos o políticas inválidas son errores.
La herramienta 0.3 no sabe leer esquema 2. Conserva ambos kits para rollback.

## Dos vistas distintas

`status` usa el working tree. En los roots declarados descubre archivos tracked
(incluso si una regla de ignore coincide) y untracked no ignorados. Consulta Git
con registros NUL y sólo inspecciona rutas/tipo fuera de las fuentes seleccionadas;
no lee ni convierte el contenido de nuevos binarios. Después compara la lista
con la cobertura efectiva. Una fuente nueva bajo un directorio ya observado
aparece en `sources.added`, no además en `coverage_check.uncovered`.

`index-check` usa exclusivamente entradas y blobs del índice, incluyendo
`GIT_INDEX_FILE` cuando Git propone un índice alternativo. Lee la política desde
el snapshot preparado, nunca desde su copia del working tree. No detecta untracked
ni afirma haberlos revisado. Los directorios vacíos no están representados en Git.
Los cambios del working tree no sustituyen al contenido preparado.

El inventario respeta las exclusiones fijas del kit (.git, .hg, .svn, .venv,
node_modules y __pycache__). Para untracked se aplican las reglas estándar de Git
disponibles en el entorno aislado del runner: reglas del repositorio y exclusiones
locales; la configuración global/sistema de Git está deshabilitada. La política
de ignore es confiable y puede cambiar qué rutas se descubren. No es un detector
de secretos ni una frontera de seguridad. Revisar archivos ignorados necesita un
alcance explícito separado.

IMPORTANTE: discovery no cambia el lector de fuentes. Un `--source src` sigue
leyendo los descendientes no excluidos por la política fija, aunque estén
ignorados por Git. Para repos con cachés dentro de src, selecciona fuentes
individuales y usa discovery para avisar sobre nuevas rutas elegibles.

## Interpretar el resultado

`coverage_check` incluye `mode`, `view`, `roots`, `state`, `eligible_count`,
`covered_count` y `uncovered` (rutas con procedencia tracked/untracked).

- `complete`: las rutas descubiertas tienen evidencia dentro de esa vista y esos
  roots. NO significa documentación correcta, pruebas aprobadas o repo completo.
- `uncovered`: existen rutas descubiertas sin cobertura. El resultado superior es
  `review_required` y el proceso termina con 1, aunque ningún hash haya cambiado.
- Un error de Git, de permisos, de esquema o una ruta insegura termina con 3.
  No se degrada silenciosamente a la comprobación anterior.

Los códigos heredados 0/1/2/3 conservan su significado. Snapshot 0 indica que se
creó la captura, no que `status` esté limpio. `--apply` se exige para esa escritura.
Los comandos de lectura no hacen staging, commits, ni escriben eventos.

El descubrimiento reutiliza el runner del gate. Conflictos, intent-to-add,
symlinks y submódulos preparados son rechazados por el lector del índice, incluso
fuera de los roots. Un symlink/FIFO descubierto tampoco se sigue. En repos que
necesiten esas características, ajustar y verificar la política es trabajo aparte.
Objetos no representados por Git (por ejemplo FIFOs untracked) no son candidatos;
este inventario no promete enumerar todos los objetos del filesystem.
Una raíz descubierta borrada o convertida en archivo es error en working tree.

Se toma un inventario antes y después de leer las fuentes para detectar cambios
concurrentes de nombres, índice o HEAD. Es una comprobación de mejor esfuerzo;
no es una transacción ni detecta todas las carreras ABA. Trabaja en un árbol quieto.
Las correcciones históricas y aprobaciones semánticas siguen fuera de este detector.

## Pruebas

```sh
python -B -m unittest discover -s tests -v
KIT_E2E_GRAPHIFY=1 python -B -m unittest discover -s tests -v
```

La segunda variante requiere Graphify 0.9.26 ya instalado. La suite nueva usa
procesos CLI y repos Git temporales para ambos estados; los fallos de lectura
concurrente se inyectan de forma controlada. Una prueba de alias de mayúsculas se
omite en filesystems que sí distinguen mayúsculas. Una omisión no es un aprobado.

Referencias del protocolo Git: https://git-scm.com/docs/git-ls-files y
https://git-scm.com/docs/gitignore. La implementación de discovery es propia;
no se añadió ningún runtime de CodeWiki ni una dependencia externa nueva.

## Cobertura y conocimiento 0.5

El mapa curado permite revisión selectiva; no transforma inventario en evidencia
semántica. `inventory_only` exige una razón y no puede solaparse con watches.
Fuentes sin relación y headings no gestionados siguen explícitos. La cobertura
parcial del piloto es un resultado válido que debe informarse, no esconderse
con relaciones fabricadas. Refrescar la línea base no elimina deuda de revisión.
