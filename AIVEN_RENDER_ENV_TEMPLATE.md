# New Aiven To Render Environment Template

Use this after creating a new Aiven MySQL service.

In Render, open:

```text
waste-backend-system > Environment
```

Replace the database values with the new Aiven values:

```env
DB_HOST=PASTE_NEW_AIVEN_HOST_HERE
DB_PORT=PASTE_NEW_AIVEN_PORT_HERE
DB_USER=avnadmin
DB_PASSWORD=PASTE_NEW_AIVEN_PASSWORD_HERE
DB_NAME=defaultdb
DB_SSL_CA=ca.pem
JWT_SECRET=PASTE_LONG_RANDOM_SECRET_HERE
FLASK_SECRET_KEY=PASTE_ANOTHER_LONG_RANDOM_SECRET_HERE
ALLOW_DEBUG_ROUTES=true
```

After saving the environment variables, redeploy Render and test:

```text
https://waste-backend-system.onrender.com/debug/ensure-tables
https://waste-backend-system.onrender.com/api/barangays
https://waste-backend-system.onrender.com/debug/create-admin
https://waste-backend-system.onrender.com/admin
```

Expected results:

```text
/debug/ensure-tables -> success JSON
/api/barangays -> barangay list
/debug/create-admin -> admin created or already exists
/admin -> login works
```

After the admin account works, set this in Render:

```env
ALLOW_DEBUG_ROUTES=false
```

Then redeploy or restart the Render service.

