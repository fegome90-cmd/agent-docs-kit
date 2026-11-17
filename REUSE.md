# Selección y procedencia · 0.3.0-rc.1

La aplicación conserva su CLI y sus módulos. Ningún runtime de Trifecta, Claude,
Gemini, pi o framework de plugins pasa a ser dependencia.

| Fuente | Reutilización seleccionada | Alcance |
|---|---|---|
| Trifecta `session_append`, commit `5399a7f47a5f3121c3855225e236907a184d270b` | Convención summary/files/commands y plantilla de lectura de una entrada. | El escritor se implementa de nuevo con validación, idempotencia y lock. |
| ssot-check, commit `5106ac6b22daad7e37d39139f6261094549df184` | Adaptación de `_extract_details` y `extract`. | MIT íntegra en `licenses/ssot-check-MIT.txt`; origen y cambios declarados en facts.py. |
| Portalocker 4.4.2 | Dependencia de bloqueo local entre procesos. | BSD-3-Clause upstream; una dependencia del writer. No se copia ni redistribuye su paquete aquí. |
| pre-commit / doc-sentinel | Lecciones del índice Git y selección de eventos. | El gate y el ejemplo de hook son implementación propia; sin copiar su almacenamiento, instalador o runtime. |

La adaptación de ssot-check rechaza capturas ambiguas en lugar de escoger la
primera. Conserva el texto exacto, incluido `+`, espacios y ceros iniciales. Se
retiran YAML, accesos a otros repositorios, fetch, normalización de números y
actualización automática. La comparación no valida el significado de la prosa.

Fuentes primarias:

- https://github.com/fegome90-cmd/trifecta_dope/blob/5399a7f47a5f3121c3855225e236907a184d270b/src/infrastructure/cli.py
- https://github.com/conorbronsdon/ssot-check/blob/5106ac6b22daad7e37d39139f6261094549df184/ssot_check.py
- https://github.com/conorbronsdon/ssot-check/blob/5106ac6b22daad7e37d39139f6261094549df184/LICENSE
- https://github.com/wolph/portalocker

Quedan fuera de este incremento: extractores de docs-drift (se detectó una
interpretación incorrecta de `argparse.store_true`), Griffe, Sybil, Pluggy y
adaptadores específicos de hosts de agentes. Hookify no se copia: sus avisos de
licencia son inconsistentes en el checkout examinado. El antiguo doc-sentinel
es sólo un antecedente: su plugin fue retirado y su escritor reescribía arrays.

La prioridad es comprobar el núcleo y una integración Git real antes de ampliar
la cobertura semántica. Las pruebas e2e del kit usan procesos, archivos, merges y
hooks reales en repositorios temporales. Las pruebas unitarias previas conservan
dobles para fallos de E/S y contratos de herramientas externas.

## Incremento 0.4

El descubrimiento y esquema v2 son implementación propia. Reutilizan el runner
Git de 0.3; no incorporan CodeWiki, analizadores semánticos ni dependencias nuevas.
La selección MIT/Portalocker anterior se mantiene sin cambios.

## Implementación 0.5

Los módulos knowledge reutilizan el lector Git/índice, discovery, fingerprint y
writer de historial existentes. No incorporan un runner Git alternativo ni
dependencias nuevas. La validación de distribución adapta el helper del
controlador de esta implementación, con guardas de rutas disjuntas y extracción
canónica; se ejecuta aparte, nunca desde unittest. El piloto usa Git sintético
y dependencias ya presentes en una copia controlada. No se promete aislamiento
por sandbox, autenticación de reviews ni una transacción frente a editores externos.
