# Deployment Guide

This project is a Flask backend/admin panel. InfinityFree free hosting cannot run Flask, so the live backend should run on Render and the InfinityFree domain should redirect to it.

## 1. Prepare GitHub

1. Create a GitHub repository for this backend folder.
2. Commit the project files.
3. Do not commit `.env`, `venv`, `__pycache__`, or `firebase_service_account.json`.

## 2. Prepare Aiven MySQL

Use an Aiven MySQL service as the live database.

Required values:

```env
DB_HOST=your-aiven-mysql-host
DB_PORT=your-aiven-port
DB_USER=avnadmin
DB_PASSWORD=your-aiven-password
DB_NAME=defaultdb
DB_SSL_CA=ca.pem
```

Keep `ca.pem` in the project root if Aiven requires SSL.

## 3. Deploy To Render

1. Open Render.
2. Create a new Web Service.
3. Connect the GitHub repository.
4. Use these values:

```text
Repository: datsnotme/waste-collection-backend-new
Name: waste-collection-backend-new
Project: leave blank
Environment: leave blank
Language: Python 3
Branch: main
Root Directory: leave blank
Instance Type: Free
```

The expected Render URL is:

```text
https://waste-collection-backend-new.onrender.com
```

If Render says the name is unavailable, choose the closest available name and use that final URL in the later steps.

5. Set build command:

```bash
pip install -r requirements.txt
```

6. Set start command:

```bash
gunicorn --workers 1 --bind 0.0.0.0:$PORT app:app
```

7. Add environment variables from `.env.render.example`.
8. Deploy.

After deploy, test:

```text
https://waste-collection-backend-new.onrender.com/api/health
https://waste-collection-backend-new.onrender.com/api/barangays
https://waste-collection-backend-new.onrender.com/admin
```

## 4. Create First Admin

Temporarily set this Render environment variable:

```env
ALLOW_DEBUG_ROUTES=true
```

Open:

```text
https://waste-collection-backend-new.onrender.com/debug/create-admin
```

Default login:

```text
Username: admin
Password: admin123
```

After the admin is created, set:

```env
ALLOW_DEBUG_ROUTES=false
```

Redeploy or restart the Render service.

## 5. Configure InfinityFree Redirect

In InfinityFree File Manager, open `htdocs`.

Upload the contents of `infinityfree_redirect`.

Replace every `REPLACE_WITH_RENDER_SERVICE` value in:

```text
infinityfree_redirect/.htaccess
infinityfree_redirect/index.html
```

with your real Render service subdomain.

Example:

```text
waste-collection-backend.onrender.com
```

Then visit:

```text
http://wastemanagemensystem.free.nf
```

It should redirect to the Render-hosted system.

## 6. Update Mobile Apps

After Render is live, update the default backend URL in both mobile apps:

```text
waste-collection-mobile/lib/core/config/api_config.dart
waste-collection-driver-mobile/lib/core/config/api_config.dart
```

Use:

```dart
static const String defaultBaseUrl = 'https://YOUR-RENDER-SERVICE.onrender.com';
```

The manual IP/server input remains available for local testing.

Then rebuild each app:

```powershell
flutter clean
flutter pub get
flutter run
```

Uninstall the old app from the phone before reinstalling.
