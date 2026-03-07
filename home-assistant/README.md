
# TadoLocal Server – Home Assistant App (Add-on)

This app runs the **TadoLocal Server** inside Home Assistant, allowing local communication with your Tado devices without relying on the cloud.

The app provides a local API that can be used by the **TadoLocal Home Assistant custom integration**.

---

# Installation

To install this app manually:

1. SSH or SFTP into your Home Assistant filesystem.
2. Navigate to the add-ons directory:

```

/addons

```

3. Create a new directory:

```

/addons/tado-local-server

```

4. Copy **all files from the `home-assistant` folder** of this repository into the `tado-local-server` directory. (Make sure run.sh is in Unix format not DOS)

File structure:

```
/addons/
└── tado-local-server/
    ├── config.yaml
    ├── Dockerfile
    ├── run.sh
    ├── icon.png
    ├── logo.png
    └── translations/
        ├── en.yaml
        └── nl.yaml
```

5. Restart Home Assistant or reload add-ons.

Then follow the Home Assistant developer guide for **installing and testing local add-ons**:

https://developers.home-assistant.io/docs/add-ons/tutorial

For your information:
The TadoServer python code will be downloaded directly from GitHub. If a new version appears you
can press the **Rebuild** button on the TadoLocal Server App page.

 
---

# Configuration

Before starting the app, configure the following options:

| Option | Description |
|------|------|
| **Bridge IP** | IP address of your Tado Bridge |
| **Bridge PIN** | HomeKit PIN code of the Tado Bridge |
| **Keep database private** | Determines where the `tado-local.db` file is stored |

## Database Location

If **Keep database private = false**

The database will be copied to:

```
/config/.storage/tado-local.db
```

This makes it accessible from Home Assistant via:

- Samba
- Terminal
- SSH

If **Keep database private = true**

The database remains inside the container:

```
/data/tado-local.db
```

---

# Accessories

Some devices require **separate pairing sessions**, for example:

- **Tado Smart AC 3+ Control**

You can add these devices in the configuration using the **Add button** and providing:

- Accessory IP address
- HomeKit PIN code

---

# First Startup

When starting the Apps (add-on) for the first time:

1. Open the TadoLocal Server App **Log** tab
2. A URL will appear that you must open to authenticate with Tado.

Alternatively:

1. Click the **Open Web UI** button on the TadoLocal Server App page.
2. Which opens the **TadoLocal Web GUI**
3. Click **Authenticate** (center top)

---

# After Authentication

After successful authentication:

- The logs will confirm authentication.
- The **TadoLocal services will start**.
- The **zones will appear** in the Web GUI.

---

# Home Assistant Integration (Devives & service)

After the add-on is running successfully, install the **TadoLocal custom integration**:

https://github.com/array81/tado-local

Follow the instructions in that repository.

When configuring the integration, use:

```

localhost

```

as the IP address.

---

# Troubleshooting

If something does not work:

1. Check the **add-on logs**
2. Verify the **Bridge IP and PIN**
3. Ensure Home Assistant can reach the Tado Bridge

