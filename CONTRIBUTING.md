# Contribuir

## Requisitos

- Python 3.11+
- `make install`

## Flujo

1. `make lint` antes de commitear.
2. `make test` antes de abrir un PR.
3. Un cambio por PR, con el motivo en el mensaje.

## Reutilizacion

Este repo sigue el patron LEGO de `kit_fundador`: modulos pequenos, opt-in, sin
dependencias innecesarias. Si hace falta una capacidad nueva, primero se busca
una libreria libre y mantenida; solo se escribe codigo propio si no existe.

## Estado

v0.5.0-rc.1 es un **WIP**, no un release. El re-audit deja 3 findings Major
abiertos. Ver `reviews/reaudit-final-round2/`.
