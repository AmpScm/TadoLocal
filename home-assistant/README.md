
# TadoLocal Server – Home Assistant App (Add-on)

![Home Assistant](https://img.shields.io/badge/Home%20Assistant-App-blue)
![Architecture](https://img.shields.io/badge/Architecture-amd64%20%7C%20armhf%20%7C%20armv7-orange)
![License](https://img.shields.io/github/license/AmpScm/TadoLocal)

This app runs the **TadoLocal Server** inside Home Assistant, allowing **local communication with Tado devices without relying on the cloud**.

The app exposes a local API that can be used by the **TadoLocal Home Assistant custom integration**.

---
# Installation
## Quick Install

Install the add-on directly into Home Assistant:

[![Add repository to Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2FAmpScm%2FTadoLocal)

Steps:

1. Click the button above
2. Click **Open Link**
3. Click **+ Add**
4. Click **Close**
5. Search for **TadoLocal Server** and click on it
6. Click **Install**
7. Follow the [Configuration](#configuration) section **before starting**

---

## Manual Installation

If you prefer installing manually:

1. SSH or SFTP into your Home Assistant filesystem
2. Navigate to:

```
/addons
```

3. Create the directory:

```
/addons/tado-local-server
```

4. Copy **all files from the `home-assistant` folder** of this repository into that directory.

Ensure `run.sh` is saved in **Unix format (LF)** and not **Windows format (CRLF)**.

Example structure:

```
/addons/
└── tado-local-server/
    ├── config.yaml
    ├── Dockerfile
    ├── run.sh
    ├── README.md
    ├── icon.png
    ├── logo.png
    └── translations/
        ├── en.yaml
        └── nl.yaml
```

5. Restart Home Assistant or reload add-ons.

Then follow the Home Assistant developer documentation:

https://developers.home-assistant.io/docs/add-ons/tutorial

---

# Configuration
## Setup Tado Internet Bridge

Before starting the add-on, configure the following options:

| Option | Description |
|------|------|
| **Bridge IP** | IP address of your Tado Bridge |
| **Bridge PIN** | HomeKit PIN code of the Tado Bridge |
| **Keep database private** | Determines where the `tado-local.db` file is stored |

---

## Database Location

If **Keep database private = false**

The database will be copied to:

```
/config/.storage/tado-local.db
```

This makes the database accessible from Home Assistant via:

- Samba
- Terminal
- SSH

If **Keep database private = true**

The database stays inside the container:

```
/data/tado-local.db
```


## Accessories

Some devices require **separate pairing sessions**, for example:

- **Tado Smart AC Control V3+**

You can add these accessories in the configuration using the **Add** button and providing:

- Accessory IP address
- HomeKit PIN code


---
# Running TadoLocal Server
## First Startup

When starting the add-on for the first time:

1. Open the **Log** tab of the TadoLocal Server add-on
2. A URL will appear that you must open to authenticate with Tado.

Alternatively:

1. Click **Open Web UI** on the TadoLocal Server App page
2. The **TadoLocal Web GUI** will open
3. Click **Authenticate** in the Web GUI. (center top)

---

## After Authentication

After successful authentication:

- The logs will confirm authentication
- The **TadoLocal services will start**
- The **zones will appear** in the Web GUI

---

# Home Assistant Integration (Devices & Services)

After the add-on is running successfully, install the **TadoLocal custom integration**:

https://github.com/array81/tado-local

Follow the instructions in that repository.

When configuring the integration, use: `localhost` as the IP address and the configured port (default **4407**).

---

# Updating

The TadoServer Python code is downloaded directly from GitHub.

When a new version becomes available, simply click **Rebuild** on the TadoLocal Server add-on page.

---

# Troubleshooting

If something does not work:

1. Check the **add-on logs**
2. Verify the **Bridge IP and PIN**
3. Ensure Home Assistant can reach the Tado Bridge

