# gtm-engine (Código Abierto)

<p align="center">
  <strong>Idioma / Language:</strong>
  <a href="README.md">English</a> |
  <a href="README.zh-CN.md">简体中文</a> |
  <a href="README.ja.md">日本語</a> |
  <strong>Español</strong> |
  <a href="README.de.md">Deutsch</a> |
  <a href="README.ko.md">한국어</a>
</p>

<p align="center">
  <a href="https://github.com/henryroxstar/gtm-engine/stargazers"><img src="https://img.shields.io/github/stars/henryroxstar/gtm-engine?style=flat&label=Stars" alt="Stars" /></a>
  <a href="https://twitter.com/intent/tweet?text=The%20open-source%20GTM%20agent%20harness%20for%20startups%3A%2063%20skills%2C%20zero%20auto-spam%2C%20runs%20locally%20in%20Claude%20Code%20or%20Antigravity.&url=https%3A%2F%2Fgithub.com%2Fhenryroxstar%2Fgtm-engine"><img src="https://img.shields.io/badge/Share%20on-X-black?style=flat&logo=x" alt="Share on X" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache_2.0-blue.svg" alt="License: Apache 2.0" /></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.11+-3776AB.svg" alt="Python 3.11+" /></a>
  <a href="https://docs.anthropic.com/en/api/agent-sdk/overview"><img src="https://img.shields.io/badge/built%20with-Claude%20Agent%20SDK-d97757.svg" alt="Built with Claude Agent SDK" /></a>
  <a href="https://modelcontextprotocol.io/"><img src="https://img.shields.io/badge/connectivity-MCP--first-6E56CF.svg" alt="MCP-first" /></a>
  <a href="#por-qué-está-construido-de-esta-manera"><img src="https://img.shields.io/badge/human%20gates-puertas%20de%20aprobaci%C3%B3n%20obligatorias-2ea44f.svg" alt="Human Gates" /></a>
  <a href="#soporte-de-entornos-y-espacios-de-trabajo"><img src="https://img.shields.io/badge/workspaces-Claude%20%7C%20Antigravity%20%7C%20Cursor%20%7C%20Codex-orange.svg" alt="Harness Support" /></a>
</p>

![GTM Content OS y Canal de Prospección Saliente](docs/assets/gtm-pipeline-flow.png)

*El entorno de ejecución de agentes de Go-To-Market (GTM) de código abierto para startups de software B2B. Diseñado para multiplicar por 10 la productividad en ventas, preventa y marketing.*

```
      .-"""-.
     /  o o  \        un solo cerebro, muchas manos cuidadosas —
     \   ^   /        tú apruebas cada contacto y publicación
      )-----(
     / /| |\ \
    ( ( | | ) )
     \_/ | \_/
        `-`
```

### Eres el fundador. También eres todo el equipo de Go-To-Market (GTM).

Llevas todos los roles, trabajas con recursos ajustados y buscas incansablemente el ajuste producto-mercado (PMF). Cerrar tratos nunca ha sido la única tarea: tienes que construir activamente el embudo de ventas; trasladar la voz del cliente al equipo de ingeniería; explicar con solvencia un producto en evolución cuya documentación quedó desactualizada hace dos semanas (sin tener que arrastrar a un ingeniero a cada reunión); y probar nuevos mensajes cada semana para destacar en el mercado.

Son cinco puestos completos. El consejo habitual es "contrata a cinco personas". Lo que realmente tienes es tu portátil, tu suscripción a un entorno de IA (Claude Desktop, Google Antigravity, Cursor o Codex) y el tiempo de esta semana.

**gtm-engine es el sistema que asume esos cinco trabajos contigo.** Prospección en frío, preparación de reuniones, planes estratégicos de cuentas, presentaciones comerciales, radares de mercado semanales y contenido multicanal adaptado a tu marca (publicaciones de LinkedIn, artículos de blog, guiones de podcast, infografías) — todo activado mediante una simple frase en el chat, con la voz auténtica de tu empresa y basado en tu base de conocimiento real.

Y lo que es fundamental: **está diseñado de tal manera que el agente no puede físicamente enviar correos, publicar contenido ni filtrar información de forma autónoma.** No te pide confianza ciega: su arquitectura garantiza que no puede extralimitarse.

Tres principios inmutables:
1. **Nada se envía ni se publica sin tu aprobación explícita.**
2. **Los datos de cada empresa se mantienen física y estrictamente aislados en su propio perfil.**
3. **El agente no tiene acceso a llamadas HTTP directas sin control ni a ejecución de comandos Shell arbitrarios.** Toda interacción exterior se canaliza a través de herramientas MCP (Model Context Protocol).

![Content OS Architecture](docs/assets/content-os-grade-a-plus-architecture.png)

---

### ¿Por qué gtm-engine? (Comparativa Arquitectónica)

| Capacidad | Plataformas comerciales "AI SDR" (11x, Artisan) | Diálogo directo con Prompts (ChatGPT / Claude) | Frameworks genéricos de agentes (CrewAI / LangChain) | **gtm-engine (Este sistema)** |
|---|---|---|---|---|
| **Coste base** | \$500 – \$3,000 / mes | \$20 / mes (con copiado y pegado manual continuo) | Consumo de tokens + costes de servidor | **\$0 base** (funciona sobre tu suscripción actual de IA) |
| **Seguridad de salida** | Envío automático de emails fríos (riesgo de reputación) | Copiado y revisión manual | Permisos amplios concedidos a herramientas | **Puertas de aprobación humana obligatorias** (imposible el autoenvío) |
| **Contexto de la empresa** | Extracción web superficial | Reexplicar el contexto en cada prompt | Requiere configurar bases de datos vectoriales complejas | **Segundo Cerebro en Perfiles** (configura una vez, hereda en todos los flujos) |
| **Variedad de flujos** | Limitado a email frío | Limitado a texto plano | Requiere programar grafos complejos a medida | **63 habilidades y 10 paquetes integrados** (vídeo, presentaciones, posts, prospección) |
| **Privacidad de datos** | Bloqueo por proveedores externos en la nube | Datos susceptibles de usarse en entrenamiento | Depende de la configuración del usuario | **100% Local / Gitignored** (la información nunca sale de tu equipo) |

---

### Inicio rápido en 30 segundos

```bash
# 1. Clona el repositorio
git clone https://github.com/henryroxstar/gtm-engine.git && cd gtm-engine

# 2. Abre esta carpeta en tu entorno de IA favorito (Claude Desktop, Google Antigravity, Cursor o Codex)

# 3. Escribe en el chat:
"set me up" --site tuempresa.com
```
*No requiere Docker, ni servidores en segundo plano, ni claves API de terceros para comenzar.*

---

## Míralo en acción (See it work)

Sin comandos complicados ni tediosos archivos de configuración previos. Abres la carpeta y escribes una sola frase. Observa cómo se transforma una tarea de lunes por la mañana:

![Míralo en acción: De la instrucción a la aprobación humana](docs/assets/see-it-work-workflows.png)

Hace un minuto tenías un documento en blanco; ahora dispones de una publicación impecable, respaldada por fuentes reales y aprobada palabra por palabra por ti.

La misma sencillez aplica para todas tus prioridades semanales:

---

## ¿Por dónde empezar?

| Tu perfil | Punto de partida recomendado | Tiempo estimado |
|---|---|---|
| **No técnico** —— Enfocado en negocio y ventas, no en código | Consulta [`END-USER-ONBOARDING.md`](END-USER-ONBOARDING.md) (sin terminal ni comandos) | 30 min |
| **Técnico / Desarrollador** —— Cómodo trabajando en el entorno local | Consulta la guía [Primeros pasos (Modo Chat)](#primeros-pasos-modo-chat) | 10 min |
| **Evaluador de Arquitectura** —— Enfocado en seguridad, flujo de control y diseño | Consulta [Por qué está construido de esta manera](#por-qué-está-construido-de-esta-manera) | 10 min |

---

## Cuatro formas de ejecución e integración

gtm-engine ofrece un núcleo compartido (`gtm_core`) a través de cuatro modalidades:

**1 · Modo Chat (Predeterminado — Cero infraestructura)**
Abre este repositorio en tu entorno de IA preferido (**Claude Desktop**, **Google Antigravity**, **Cursor** o **Codex**) y escribe `"set me up"`. Todas las habilidades se ejecutan localmente, leyendo el perfil de tu empresa y con tu estilo de comunicación. Sin servidores, sin Docker y sin bases de datos adicionales. → [Primeros pasos (Modo Chat)](#primeros-pasos-modo-chat)

**2 · Agente autónomo autoalojado (24/7)**
Despliega el entorno de ejecución basado en **Claude Agent SDK** (en local o en tu propio **VPS**): monitorea noticias → planifica → investiga → crea → prepara publicaciones de forma continua, deteniéndose en las dos puertas de aprobación de Telegram. Detalles en [`docs/DEPLOY.md`](docs/DEPLOY.md).

**3 · Desarrollo de API REST para clientes**
Inicia el backend FastAPI local (`./scripts/stack.sh start`, en el puerto `:8000`) con Postgres y Redis. Pensado para desarrolladores que construyen aplicaciones cliente, paneles web o aplicaciones móviles.

**4 · Servidor MCP de GTM entrante**
Expone herramientas seleccionadas de GTM Engine mediante FastMCP HTTP (`deploy/Dockerfile.mcp`, puerto `:8001`) con autenticación mediante API Key (`sk-...`). Permite que flotas de agentes externos (como otras instancias de Claude, LangChain, AutoGen o CrewAI) consuman las herramientas de GTM de manera remota.

### Elección de ruta (evita confusiones)

| Ruta | Destinatarios | Cómo se ejecuta | Qué NO hacer |
|---|---|---|---|
| **Modo Chat (Predeterminado)** | Fundadores, ventas y marketing | Abre el repo en tu entorno de IA $\rightarrow$ escribe `"set me up"` | **No** inicies Docker ni ejecutes `./scripts/stack.sh`. ¡Es completamente serverless! |
| **Agente autoalojado** | Equipos que requieren automatización 24/7 | Despliega Docker Compose siguiendo [`docs/DEPLOY.md`](docs/DEPLOY.md) | **No** esperes chat interactivo; corre en segundo plano y avisa por Telegram. |
| **Desarrollo API REST** | Ingenieros creando interfaces personalizadas | Ejecuta `./scripts/stack.sh start` | **No** lo inicies si solo deseas utilizar las habilidades en el chat interactivo. |
| **Servidor MCP entrante** | Conexión de agentes externos a herramientas GTM | Despliega el contenedor FastMCP en el puerto 8001 | **No** lo expongas públicamente sin autenticación de API key y límites de presupuesto. |

### Soporte de entornos y espacios de trabajo

| Entorno / Plataforma | Nivel de soporte | Carga de habilidades | Observaciones |
|---|---|---|---|
| **Claude Desktop / Code** | Nativo | Plugin (`plugin/`) | Soporte integral para las 63 habilidades, MCPs y puertas interactivas |
| **Google Antigravity** | Nativo | Autodescubrimiento vía `.agents/` | Flujos multiagente, `run_command` nativo y herramientas de archivos |
| **Cursor / Codex** | Totalmente compatible| `.agents/AGENTS.md` + reglas | Modo conversacional; llamada a habilidades por convención de prompts |
| **Headless VPS (Agent SDK)**| Entorno dedicado | Bucle de agente en contenedor | Ejecución continua 24/7 supervisada mediante bots de Telegram |

---

## Primeros pasos (Modo Chat)

**Requisitos previos:** Python 3.11+ y [`uv`](https://docs.astral.sh/uv/). En el Modo Chat, el agente te guiará en la instalación automáticamente en el Paso 1.

**Paso 1 — Inicialización del motor y del Perfil de empresa (Profile)**
Escribe en el chat `"set me up"`. El sistema verificará el entorno y, mediante una breve interacción basada en la URL de tu empresa, extraerá tu perfil corporativo (marca, propuesta de valor, ICP, competidores y productos).

**Paso 2 — Configuración de herramientas y claves (opcional)**
Todas las conexiones con proveedores externos son opcionales (por defecto se recurre a búsqueda web pública sin claves):
- **Conectores de prospección** (Vibe, RocketReach, Apollo): Permiten obtener correos verificados y señales de intención de compra.
- **Conector de extracción web** (Firecrawl): Para renderizado profundo de páginas dinámicas en JavaScript.
- **Definición de límites presupuestarios**: Configura topes mensuales y por ejecución para asegurar que ninguna llamada de pago supere tu presupuesto establecido.

#### Comprobación de salud del entorno (`check_env`)
Ejecuta la herramienta de diagnóstico para verificar que tu perfil, herramientas y presupuestos estén listos:
```bash
uv run python -m gtm_core.check_env
```

---

## ¿Qué necesitas hacer hoy? (Índice rápido de tareas)

| Tu objetivo inmediato | Qué escribir en el chat | Habilidades activadas | Entregables generados |
|---|---|---|---|
| **Identificar cuentas ideales y decisores** | `"find prospects in [sector/mercado]"` | `prospect`, `draft-outreach` | Dossier evaluado, CSV para CRM, contactos verificados |
| **Preparar una llamada comercial clave** | `"prep me for my call with [empresa]"` | `call-prep`, `account-dossier` | Resumen ejecutivo en 5 min, preguntas SPIN, casos de éxito |
| **Publicar contenido de alto valor en LinkedIn**| `"draft my LinkedIn post about [tema]"` | `content-radar`, `content-studio` | 3 ganchos (Puerta 1) $\rightarrow$ borrador revisado (Puerta 2) |
| **Participar en debates técnicos del sector** | `"reply to this post: [URL]"` | `linkedin-reply`, `reddit-reply` | Respuestas de alto valor sin tono promocional (a revisar) |
| **Elaborar una propuesta técnica de solución** | `"design the solution for [empresa]"` | `solution-discovery`, `solution-design`| Documento formal de Arquitectura de Solución (SAD) |
| **Diseñar el plan estratégico de una cuenta** | `"build an account plan for [empresa]"` | `account-plan` | Cuadro de mando MEDDPICC, mapa de influencia, plan de acción |
| **Supervisar el estado del sistema** | `"run environment check"` | `check_env` CLI | Informe de conectores, integridad del perfil y presupuesto |

> Para consultar el catálogo completo de las 63 habilidades, revisa [`docs/SKILLS.md`](docs/SKILLS.md).

---

## Por qué está construido de esta manera

1. **Las dos puertas de aprobación humana no se pueden saltar.**
   El sistema se detiene en la **Puerta 1** (aprobación de enfoque) y en la **Puerta 2** (aprobación exacta del contenido final). La configuración global impone `autopublish: false`, impidiendo cualquier envío automático sin intervención humana.
2. **Aislamiento físico multitenant.**
   Cada empresa cuenta con su propio directorio en `profiles/<perfil>/`, lo que permite gestionar múltiples organizaciones sin contaminación de datos.
3. **El modelo es el cerebro; los servidores MCP son las únicas manos.**
   El agente no realiza llamadas HTTP descontroladas. Todo acceso a la red se filtra a través de herramientas MCP autorizadas, manteniendo las claves fuera del contexto del modelo.
4. **Cero nombres ni datos codificados en el motor.**
   El código es totalmente neutral y carga la información de marca en tiempo de ejecución desde el perfil seleccionado.

---

## Historial de estrellas (Star History)

[![Star History Chart](https://api.star-history.com/svg?repos=henryroxstar/gtm-engine&type=Date)](https://star-history.com/#henryroxstar/gtm-engine&Date)

---

## Licencia

Este proyecto está disponible bajo la licencia **Apache License 2.0** — consulta [`LICENSE`](LICENSE) para más detalles.
