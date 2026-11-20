# Mobile Radar Notifier

## System Overview
The **Mobile Radar Notifier** is an automated system that tracks mobile radar locations on the Donosti radar website and sends real-time notifications to Telegram users. It operates autonomously using **GitHub Actions**, executing daily checks and maintaining a persistent user database in **MongoDB Atlas**.

### Purpose and Scope
The system has two main functions:

1. **Radar Monitoring:** Web scraping of radar location data with automated image capture.
2. **User Notification:** Telegram bot integration for message delivery and user management.

For configuration, see [Configuration](#configuration) and [Development](#development).

### System Description
The system runs daily at **05:50 UTC** (06:50 / 07:50 in Spain, winter / summer), performing sequential operations to sync user data and check radar locations. When radars are detected, map images are captured and notifications with location details are sent to all active Telegram users. Users who block the bot are marked as inactive automatically. If the radar website cannot be read, only the administrator is notified and the run fails. All monitoring activities and interactions are logged in MongoDB for persistence and audit purposes.

---

## Core Functionality

| Function        | Implementation                           | Primary Component                |
|-----------------|-----------------------------------------|---------------------------------|
| Web Scraping    | Selenium WebDriver (Chrome headless)    | `telegram_radar_notifier.py`    |
| User Management | Telegram API with activity tracking     | `bot_interactions_updater.py`   |
| Image Processing| Canvas extraction & cropping with Pillow | `telegram_radar_notifier.py`    |
| Data Persistence| MongoDB Atlas                           | Both components                 |

---

## System Architecture Overview
![System Architecture](https://github.com/user-attachments/assets/0516d768-f51d-4254-80e3-7f1b092529e6)

---

## Execution Flow and Data Pipeline
![Execution Flow](https://github.com/user-attachments/assets/ed78489e-e062-4210-9f43-8fb3b43d7421)

---

## Component Responsibilities

### Primary Components

**telegram_radar_notifier.py**
- Chrome WebDriver initialization via `inicializar_driver()`
- Web scraping: `comprobar_radares()` and `extraer_canvas()`
- Telegram messaging: `enviar_mensaje_telegram()` and `enviar_imagen_telegram()` (users returning 403 are marked inactive)
- Admin alerts: `enviar_aviso_admin()`
- MongoDB logging: `registrar_monitoreo_mensajes()`

**bot_interactions_updater.py**
- Telegram API polling: `obtener_interacciones()`
- Incremental polling: last processed `update_id` stored in the `bot_state` collection
- Data transformation: `transformar_a_estructura_mongo()`
- MongoDB persistence: `guardar_interacciones_en_bd()`

---

## Execution Environment
- **GitHub Actions Workflow** orchestrates execution:
  - Scheduled triggers: `cron: '50 5 * * *'`
  - Sequential job execution in `scraping_and_sync`
  - Environment variable injection for secrets
  - Python 3.12 runtime with dependency installation
- **CI workflow** (`ci.yml`) runs lint and tests on every push and pull request
- **Dependabot** proposes monthly updates for Python packages and GitHub Actions

---

## Technology Stack

| Technology    | Purpose                  | Usage Pattern                       |
|---------------|-------------------------|------------------------------------|
| Selenium      | Web automation          | Chrome WebDriver for radar scraping |
| Pillow        | Image processing        | Canvas extraction and PNG manipulation |
| PyMongo       | Database connectivity   | MongoDB Atlas operations           |
| Requests      | HTTP communication      | Telegram API calls                  |
| BeautifulSoup | HTML parsing            | Web content analysis                |

The system runs entirely in the **GitHub Actions Ubuntu environment**, requiring no persistent infrastructure beyond MongoDB Atlas and Telegram bot credentials.

---

## Configuration

Repository secrets (Settings → Secrets and variables → Actions):

| Secret | Description |
|---|---|
| `TELEGRAM_TOKEN` | Telegram bot token |
| `DONOSTI_RADAR_WEB` | URL of the radar page |
| `MONGO_URI`, `MONGO_DB` | MongoDB Atlas connection and database |
| `MONGO_COLLECTION_INTERACTIONS`, `MONGO_COLLECTION_REPORTS` | Collection names |
| `ADMIN_CHAT_ID` | *(Optional)* Telegram chat that receives error alerts |

| `MONGO_COLLECTION_EJECUCIONES` | *(Optional)* Collection with one record per scraping run; default `ejecuciones_scraper` |

> MongoDB Atlas pauses free clusters after long inactivity: if the run fails with a DNS error for `_mongodb._tcp...`, resume the cluster in Atlas.

---

## Diagnosis and run history

Every run (successful or not) stores one document in the `ejecuciones_scraper` collection:
result (`ok_sin_radares`, `ok_con_radares`, `fallo`), cause, duration, each wait attempt, and a
network probe (runner IP, DNS/TCP/TLS/HTTP timings per phase, HTTP status). On failure a second
probe is taken, and the admin alert states the cause (see `diagnostico_web.py`):

| Cause | Meaning |
|---|---|
| `red_dns`, `red_tcp_timeout`, `red_tcp_rechazada`, `red_tls` | The site is not reachable from the runner (phase shown) |
| `http_timeout`, `http_error_conexion`, `http_<code>` | Connection works but the HTTP request fails or returns an error status |
| `navegador_err_*` | Plain HTTP works but Chrome shows a network error page (e.g. `ERR_TIMED_OUT`) |
| `contenido_no_encontrado` | Page loads fine but the expected text is not there: the site may have changed (compare `contenido.hash`/`inicio`) |
| `estructura_desconocida`, `chrome_no_inicia`, `error_interno` | Scraper-side problems |

On failure the page HTML (secret URL, path and query redacted) is uploaded as the
`evidencias-fallo` artifact (30 days). Screenshots are off by default (they cannot be redacted);
set `EVIDENCIAS_CAPTURA=1` to include them. The manual workflow `diagnostico_scraper.yml` runs the probe and
the scraper without sending anything to users.

---

## Deferred retries and final notice

The workflow runs at 05:50 UTC and again at 06:20, 07:00 and 08:00 UTC. The extra runs
(`REINTENTO_DIFERIDO=1`) exit immediately if `ejecuciones_scraper` already has a successful run
today, so users never get a duplicate. The 08:00 run (`ULTIMO_INTENTO=1`) is the last: if the site
could still not be checked all day, it sends the users one message saying so, with a link to the
official page (`URL_WEB_RADARES`), because silence could be mistaken for "no radars". It is sent at
most once a day. Manual runs (`workflow_dispatch`) always run in full and never send that notice.
Runs are serialized with a `concurrency` group, so a delayed cron cannot overlap the next one. GitHub may
skip a scheduled run under load: if the 08:00 run is skipped and everything failed before, no notice is sent.
If you change the last cron, change the `ULTIMO_INTENTO` condition in the workflow too (a test checks it).

---

## Development

```bash
pip install -r requirements-dev.txt
ruff check .
pytest -q
```

---

## Sources
- `.github/workflows/mobile_radar_notifier.yml`
- `telegram_radar_notifier.py`
- `bot_interactions_updater.py`
- `requirements.txt`
