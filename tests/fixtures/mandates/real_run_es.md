# Mandato — refactor del intérprete de consultas y del endpoint de consulta

## Contexto

El servicio de consultas recibe una pregunta en lenguaje natural, la pasa por el intérprete
(`app/services/interpreter.py`) y devuelve una respuesta estructurada desde el endpoint
`POST /consult` (`app/routers/consult.py`). Desde la última versión, las respuestas llegan
incompletas cuando la pregunta trae más de una intención: el intérprete se queda con la primera
y descarta el resto en silencio. El equipo de producto lo detectó en las pruebas con usuarios y
lo confirmó con los registros de la semana pasada: cerca del 18% de las consultas compuestas
pierden al menos una intención, y ninguna de ellas deja rastro en los registros.

Este mandato pide corregir ese comportamiento en cinco fases, en orden. Cada fase deja el
repositorio en verde antes de pasar a la siguiente. Las fases están pensadas para que un revisor
pueda seguir el cambio paso a paso, aunque la entrega final es un único cambio coherente.

Reglas generales para todo el mandato:

- Mantén la API pública del endpoint: mismos campos de entrada, mismos códigos de estado.
- Cada cambio de comportamiento lleva su prueba de regresión en `tests/test_consult.py`.
- Las funciones nuevas llevan tipos completos; el proyecto usa mypy en modo estricto.
- Nada de dependencias nuevas: la biblioteca estándar y lo que ya está en el proyecto bastan.
- Si una decisión no está clara, elige la opción más conservadora y anótala en el informe final.
- Los mensajes de registro van en inglés, como el resto del proyecto; el informe, en español.

### Ejemplo de consulta compuesta

Petición que llega hoy al endpoint:

```json
{
  "question": "¿Cuánto cuesta el plan anual y puedo pagarlo en dos cuotas?",
  "language": "es"
}
```

Respuesta actual, que pierde la segunda intención:

```json
{
  "answers": [{"intent": "plan_price", "plan": "annual"}],
  "language": "es"
}
```

Respuesta esperada al terminar el mandato:

```json
{
  "answers": [
    {"intent": "plan_price", "plan": "annual"},
    {"intent": "payment_split", "installments": 2}
  ],
  "language": "es",
  "truncated": false
}
```

## Fase 1 — Auditoría

Objetivo: entender el flujo actual de punta a punta y dejarlo por escrito antes de cambiarlo.

El recorrido de una consulta es este:

1. El router valida el cuerpo de la petición y llama al intérprete con el texto y el idioma.
2. El intérprete normaliza el texto, lo divide en frases y busca intenciones conocidas en cada
   una, con el catálogo de patrones que vive al principio del módulo.
3. Por cada intención reconocida construye un objeto de respuesta parcial con sus parámetros.
4. El router une las respuestas parciales y devuelve el resultado al cliente.

El fallo está en el paso 3. Ver `app/services/interpreter.py:287-289`: ahí el bucle que recorre
las frases hace `break` en cuanto reconoce la primera intención, así que las frases siguientes
nunca se miran. La función `interpret` se llama desde `app/routers/consult.py:67`, que asume
que siempre recibe una lista con un solo elemento y toma `result[0]` sin comprobar nada más.

Hay dos referencias más que conviene revisar durante la auditoría:

- El modelo antiguo de `app/models/legacy.py:12` define la misma estructura de respuesta con
  otro nombre. Comprueba si algo lo sigue importando; si nada lo usa, anótalo como candidato a
  retirar en una entrega posterior, pero no lo retires en esta.
- Una nota antigua del equipo apunta a `app/routers/consult.py:900` como el lugar donde se
  unían las respuestas. Ese archivo ya no tiene tantas líneas; confirma dónde ocurre hoy la
  unión y corrige la referencia en el informe.

Entregable de esta fase: el informe de auditoría en `docs/audits/auditoria-interprete.md`, con
el recorrido completo, las líneas exactas de cada paso, las dependencias del modelo antiguo y
una lista de riesgos ordenada de mayor a menor. El informe incluye también un ejemplo real de
consulta compuesta (inventado a partir de los registros, sin datos de clientes) y la respuesta
que devuelve hoy el endpoint, para que la fase de pruebas pueda partir de él.

Durante la auditoría, revisa también el catálogo de patrones del intérprete. Cada entrada asocia
una intención con sus expresiones y con los parámetros que extrae; algunas entradas se solapan
(por ejemplo, el precio de un plan y el precio de un complemento comparten varias expresiones),
y hoy el corte en la primera intención esconde ese solapamiento. Lista en el informe las
entradas que se solapan, con un ejemplo de pregunta para cada par, y propón cómo desempatarlas.
No reordenes el catálogo en esta entrega: el orden actual es parte del comportamiento que las
pruebas de la Fase 4 tienen que fijar antes de cualquier cambio de orden.

## Fase 2 — Corrección del intérprete

Objetivo: que el intérprete devuelva todas las intenciones de la pregunta, en el orden en que
aparecen, conservando el formato de cada respuesta parcial.

### Paso 2.1 — Recorrer todas las frases

Sustituye el `break` del bucle por una acumulación: cada frase aporta cero o más intenciones y
el resultado es la lista de todas ellas. Si dos frases producen la misma intención con los
mismos parámetros, consérvala una sola vez (la primera). Como en la Fase 1, deja constancia en
el informe de cualquier comportamiento que dependiera del corte anterior.

### Paso 2.2 — Límites razonables

Una pregunta enorme no debe producir una lista sin fin. Añade un límite configurable de
intenciones por consulta, con un valor por defecto de ocho, leído del mismo objeto de ajustes
que ya usa el intérprete. Cuando el límite corta la lista, el resultado lo indica con un campo
booleano `truncated`, y el registro deja una línea de aviso con el número de intenciones
descartadas.

### Paso 2.3 — Tipos y nombres

La función devuelve hoy un tipo genérico. Cámbialo por una lista tipada de respuestas parciales
y renombra las variables de una letra del bucle por nombres que digan lo que contienen. Las
funciones auxiliares que solo usa el intérprete pasan a ser privadas del módulo.

### Paso 2.4 — Registro

Cada consulta deja una única línea de registro con el número de frases, el número de
intenciones reconocidas y, si hubo corte, cuántas se descartaron. La línea no incluye el texto
de la pregunta: los registros se comparten con soporte y la pregunta puede traer datos
personales. Usa el registrador del módulo, con el nivel que ya usa el resto del intérprete.

## Fase 3 — Endpoint de consulta (`app/routers/consult.py:67`)

Objetivo: que el endpoint una todas las respuestas parciales que devuelve el intérprete.

- Elimina el acceso directo a `result[0]` y recorre la lista completa.
- La respuesta del endpoint mantiene su forma: un objeto con `answers` (lista) y `language`.
  Antes, `answers` siempre tenía un elemento; ahora tiene tantos como intenciones.
- Si el intérprete marca `truncated`, el endpoint lo propaga en la respuesta con el mismo
  nombre y con el mismo código de estado.
- Si el intérprete no reconoce ninguna intención, el endpoint responde como hoy: lista vacía y
  código 200, con el campo `reason` que ya existe.

Revisa también el manejo de idiomas: la consulta puede llegar en español o en inglés, y el
endpoint debe pasar el idioma al intérprete tal como llega, sin normalizarlo dos veces. Hoy se
normaliza en el router y otra vez en el intérprete; deja la normalización solo en el
intérprete y anota el cambio en el informe.

Los errores del intérprete siguen el camino de hoy: el router los convierte en una respuesta 422
con el mensaje que ya existe. Una respuesta parcial inválida descarta solo esa intención, nunca
la consulta completa, y el registro dice cuál se descartó. El tiempo de respuesta del endpoint
no debe crecer de forma apreciable con la nueva unión: mídelo con el ejemplo de la auditoría
antes y después del cambio y anota las dos cifras en el informe.

## Fase 4 — Pruebas

Objetivo: dejar el comportamiento nuevo cubierto por pruebas que fallen con el código anterior.

Añade en `tests/test_consult.py`, como mínimo:

1. Una consulta con dos intenciones devuelve dos respuestas, en el orden de la pregunta.
2. Una consulta con la misma intención repetida devuelve una sola respuesta.
3. Una consulta con más intenciones que el límite devuelve el límite y `truncated` verdadero.
4. Una consulta sin intenciones reconocidas devuelve la lista vacía con el `reason` de siempre.
5. El idioma llega al intérprete sin normalizarse en el router.
6. El ejemplo real de la auditoría devuelve ahora todas sus intenciones.

Cada prueba nombra el comportamiento que protege. Ejecuta la batería completa con
`pytest tests/test_consult.py` y después la suite entera; las dos terminan en verde. Si alguna
prueba existente dependía del corte en la primera intención, corrígela y explica por qué en el
informe final, con la línea de la prueba y el motivo.

Las pruebas usan los datos de ejemplo que ya existen en el proyecto. Si hace falta uno nuevo,
créalo junto a los demás con el mismo formato y un nombre que diga qué caso cubre.

Añade además una prueba de rendimiento sencilla: cien consultas compuestas seguidas terminan en
menos de un segundo en una máquina de desarrollo normal. Si el proyecto ya marca este tipo de
pruebas con un marcador propio, úsalo, para que la suite rápida siga siendo rápida.

## Fase 5 — Documentación

Objetivo: que quien llegue después entienda el cambio sin leer el código.

Al final, el docs-updater registra los cambios:

- Actualiza la guía del endpoint en `docs/` con el nuevo significado de `answers` y el campo
  `truncated`, con un ejemplo de consulta compuesta y su respuesta.
- Añade una entrada en `CHANGELOG.md`, en la sección sin publicar, que diga en una línea qué
  cambia para quien llama al endpoint.
- Completa el informe de `docs/audits/auditoria-interprete.md` con lo que se hizo en cada fase
  y las decisiones tomadas, enlazando las pruebas nuevas.

La documentación va en el mismo tono que la existente: frases cortas, ejemplos concretos y sin
promesas sobre versiones futuras.

## Cómo trabajar

Sigue las fases en orden y termina cada una con la suite en verde. Al cerrar una fase, escribe
en el informe un párrafo corto: qué cambió, qué pruebas lo cubren y qué se dejó para después.
Si una fase descubre algo que obliga a rehacer otra anterior, vuelve a ella, corrígela y deja
constancia del motivo; no acumules arreglos pendientes para el final. Propón un mensaje de
commit por fase en el informe, pero no ejecutes ninguna operación de git.

## Notas para el revisor

- El cambio de comportamiento visible es uno solo: `answers` puede tener más de un elemento.
- El campo `truncated` es nuevo y opcional para quien llama; su ausencia equivale a falso.
- La normalización del idioma pasa del router al intérprete; el resultado es el mismo.
- El modelo antiguo sigue en su sitio; la auditoría dice si alguien lo importa todavía.
- La referencia antigua a la línea 900 del router se corrige solo en el informe.

## Criterios de aceptación

1. El endpoint devuelve todas las intenciones de una consulta compuesta, en orden.
2. El límite de intenciones es configurable y su corte se ve en la respuesta y en el registro.
3. Las seis pruebas nuevas fallan con el código anterior y pasan con el nuevo.
4. La suite completa termina en verde y mypy en modo estricto no informa errores nuevos.
5. La auditoría, la guía del endpoint y el CHANGELOG reflejan el cambio.

## Entregables

- El código corregido del intérprete y del endpoint.
- Las pruebas nuevas en `tests/test_consult.py`.
- El informe de auditoría en `docs/audits/auditoria-interprete.md`.
- La guía del endpoint actualizada y la entrada del CHANGELOG.
- Un informe final breve: qué se hizo en cada fase, qué quedó pendiente y por qué.
