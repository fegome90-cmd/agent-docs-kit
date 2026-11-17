<!-- agent-docs-kit:draft -->
# Composición y límites

## Intención

{{¿Qué decisiones estructurales responden a qué necesidades verificadas? Separa diseño vigente de propuestas.}}

## Componentes

| Componente y ubicación | Responsabilidad y datos que posee | Lo que delega | Dependencias e interfaces |
|---|---|---|---|
| {{Componente}} | {{Propiedad concreta}} | {{Límite}} | {{Proveedores, consumidores y contrato}} |

## Flujos y estado

{{Traza un recorrido representativo desde la entrada hasta la salida. Identifica dónde vive el estado, quién lo cambia y qué cruza límites de confianza, si aplica. Enlaza evidencia.}}

## Invariantes y evolución

{{¿Qué debe mantenerse al cambiar el sistema? ¿Qué dependencias están permitidas y dónde se comprueban? Incluye solo reglas respaldadas y enlaza al propietario normativo si existe.}}

## Comprobación y riesgos

{{Relaciona invariantes con pruebas o controles existentes y enlaza RUNTIME.md para sus comandos. Registra limitaciones observadas sin convertirlas en prohibiciones nuevas.}}

## Evidencia y pendientes

{{Indica fuentes y contradicciones abiertas. El mapa de ubicación vive en CODEMAP.md; evita duplicar allí este contrato.}}
