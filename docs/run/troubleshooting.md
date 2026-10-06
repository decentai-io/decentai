# Troubleshooting

What goes wrong when DecentAI is started, and where to look.

## It does not start

- **The script says Docker did not answer.** `docker compose` is not
  installed.
- **The script says Docker is not running.** Start Docker Desktop, or
  the Docker service on Linux, and run the script again.
- **The port is taken.** `docker compose` says the address is already in
  use: delete `deploy.env` if nothing was started with it yet and run
  the script with `--port`, or change `PORT`, `PUBLIC_APP_URL` and
  `CORS_ALLOW_ORIGINS` in it together.
- **The seeder failed.** `docker compose --env-file deploy.env logs init`.
  A wrong `MONGO_URI` password shows here, as an authentication error:
  the seeder is the first to connect, and the backend does not start
  until it has finished.
- **The backend restarts.** `docker compose --env-file deploy.env logs
  backend`. A missing `TOKEN_SECRET_KEY` says so in the first lines.
- **Chats say the runtime is unavailable.** `docker compose --env-file
  deploy.env logs ai-runtime`. The runtime refuses connections whose
  signature does not verify: `BACKEND_SERVICE_PUBLIC_KEY` must be the
  pair of the backend's private key.
- **The address opens the sign-in page with a refusal.** The first
  person's password was changed since `deploy.env` was written: sign in
  with the one it was changed to.

## Where the logs are

```bash
docker compose --env-file deploy.env ps
docker compose --env-file deploy.env logs -f backend      # or ai-runtime, agents, init, caddy, mongo
```

What agents did — each worker's start and end, its log, every
connection it made or was refused — is under **Settings → Monitoring**
([what is written down](../system/monitoring.md)).
