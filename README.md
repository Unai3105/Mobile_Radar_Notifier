# Mobile Radar Notifier

## System Overview
The **Mobile Radar Notifier** is an automated system that tracks mobile radar locations on the Donosti radar website and sends real-time notifications to Telegram users. It operates autonomously using **GitHub Actions**, executing daily checks and maintaining a persistent user database in **MongoDB Atlas**.

### Purpose and Scope
The system has two main functions:

1. **Radar Monitoring:** Web scraping of radar location data with automated image capture.
2. **User Notification:** Telegram bot integration for message delivery and user management.

For more detailed information on components and deployment, see the [Operations Guide](#) and [Technical Reference](#).

### System Description
The system runs daily at **06:50 UTC**, performing sequential operations to sync user data and check radar locations. When radars are detected, map images are captured and notifications with location details are sent to all registered Telegram users. All monitoring activities and interactions are logged in MongoDB for persistence and audit purposes.

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
<!-- Insert image here: System Architecture Overview -->
![System Architecture](https://github.com/user-attachments/assets/0516d768-f51d-4254-80e3-7f1b092529e6)

---

## Execution Flow and Data Pipeline
<!-- Insert image here: Execution Flow and Data Pipeline -->
![Execution Flow](path/to/execution_flow_image.png)

---

## Component Responsibilities

### Primary Components

**telegram_radar_notifier.py**
- Chrome WebDriver initialization via `inicializar_driver()`
- Web scraping: `comprobar_radares()` and `extraer_canvas()`
- Telegram messaging: `enviar_mensaje_telegram()` and `enviar_imagen_telegram()`
- MongoDB logging: `registrar_monitoreo_mensajes()`

**bot_interactions_updater.py**
- Telegram API polling: `obtener_interacciones()`
- User activity validation: `obtener_estado_usuario()`
- Data transformation: `transformar_a_estructura_mongo()`
- MongoDB persistence: `guardar_interacciones_en_bd()`

---

## Execution Environment
- **GitHub Actions Workflow** orchestrates execution:
  - Scheduled triggers: `cron: '50 5 * * *'`
  - Sequential job execution in `scraping_and_sync`
  - Environment variable injection for secrets
  - Python 3.10.11 runtime with dependency installation

---

## Technology Stack

| Technology    | Purpose                  | Usage Pattern                       |
|---------------|-------------------------|------------------------------------|
| Selenium      | Web automation          | Chrome WebDriver for radar scraping |
| Pillow        | Image processing        | Canvas extraction and PNG manipulation |
| PyMongo       | Database connectivity   | MongoDB Atlas operations           |
| Requests      | HTTP communication      | Telegram API calls                  |
| BeautifulSoup | HTML parsing            | Web content analysis                |
| PyTZ          | Timezone handling       | UTC timestamp management            |

The system runs entirely in the **GitHub Actions Ubuntu environment**, requiring no persistent infrastructure beyond MongoDB Atlas and Telegram bot credentials.

---

## Sources
- `.github/workflows/mobile_radar_notifier.yml`
- `telegram_radar_notifier.py`
- `bot_interactions_updater.py`
- `requirements.txt`
