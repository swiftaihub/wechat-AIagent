# Deploy Web UI to Public Internet

This guide upgrades the local `/ui` chat page to a production deployment target.

## 1. Required Environment Variables

At minimum:

```dotenv
PORT=8787
WEBUI_HOST_PORT=8788
MAX_INPUT_CHARS=4000
MAX_OUTPUT_TOKENS=800
WEBUI_BASE_PATH=/ui/herbal_advice
WEBUI_TITLE=Herbal Tea Recommendation Helper
WEBUI_TITLE_ZH=草本茶推荐助手
WEBUI_TITLE_EN=Herbal Tea Recommendation Helper
WEBUI_WELCOME_MESSAGE_ZH=欢迎来到品牌 AI Helper。你可以告诉我最近的状态、送礼方向，或想先了解哪类草本茶。
WEBUI_WELCOME_MESSAGE_EN=Welcome to the brand AI helper. Share how you have been feeling, what you might want to gift, or the tea direction you want to explore.
WEBUI_API_BASE_URL=/ui/herbal_advice/api/chat
WEBUI_CORS_ALLOWED_ORIGINS=
DASHSCOPE_API_KEY=
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
DASHSCOPE_MODEL=qwen-flash
RATE_LIMIT_MAX_REQUESTS=8
MAX_MESSAGES_PER_USER_SESSION=20
MAX_REQUESTS_PER_HOUR=40
MAX_REQUESTS_PER_DAY=200
```

If your frontend and API are on different domains, set:

```dotenv
WEBUI_CORS_ALLOWED_ORIGINS=https://your-ui-domain.com
```

Use comma-separated values for multiple origins.

## 2. Local Docker Validation

```bash
docker build -t wechat-aiagent:latest .
docker run --rm -p 8788:8787 --env-file .env wechat-aiagent:latest
```

Check:

- `http://localhost:8788/health`
- `http://localhost:8788/ui/herbal_advice`

## 3. VPS Path: Docker + Nginx Reverse Proxy

### 3.1 Start app container

```bash
docker run -d --name wechat-aiagent \
  --restart unless-stopped \
  --env-file .env \
  -p 127.0.0.1:8787:8787 \
  wechat-aiagent:latest
```

### 3.2 Nginx site config

`/etc/nginx/sites-available/wechat-aiagent.conf`

```nginx
server {
    listen 80;
    server_name chat.yourdomain.com;

    client_max_body_size 10m;

    location / {
        proxy_pass http://127.0.0.1:8787;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
    }
}
```

Enable and reload:

```bash
sudo ln -s /etc/nginx/sites-available/wechat-aiagent.conf /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

### 3.3 HTTPS (recommended)

```bash
sudo apt-get update && sudo apt-get install -y certbot python3-certbot-nginx
sudo certbot --nginx -d chat.yourdomain.com
```

Public URL example:

- `https://chat.yourdomain.com/ui`
- `https://chat.yourdomain.com/ui/herbal_advice`

## 4. PaaS Path A: Render

1. Push repository to GitHub.
2. Render -> New -> Web Service -> connect repository.
3. Runtime: `Docker`.
4. Set environment variables in Render dashboard (`WEBUI_WELCOME_MESSAGE`, `DASHSCOPE_API_KEY`, protection limits, etc.).
5. Expose port `8787` (Render typically injects `PORT`; app already supports `PORT`).
6. Deploy and open generated URL:
   - `https://<service-name>.onrender.com/ui`

Notes:

- Keep `DASHSCOPE_API_KEY` in the platform secret store rather than committing it into files.
- For separate frontend/API domains, configure `WEBUI_CORS_ALLOWED_ORIGINS`.

## 5. PaaS Path B: Fly.io

1. Install and login:

```bash
fly auth login
```

2. Initialize app in repo:

```bash
fly launch --no-deploy
```

3. Set env vars:

```bash
fly secrets set WEBUI_API_BASE_URL=/ui/herbal_advice/api/chat
fly secrets set WEBUI_BASE_PATH=/ui/herbal_advice
fly secrets set DASHSCOPE_API_KEY=<your-model-studio-key>
fly secrets set DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
fly secrets set DASHSCOPE_MODEL=qwen-flash
```

4. Deploy:

```bash
fly deploy
```

5. Open:

```text
https://<app-name>.fly.dev/ui/herbal_advice
```

## 6. Observability and Health

- Container health endpoint: `GET /health`
- Docker healthcheck is included in `Dockerfile` and `docker-compose.yml`
- Logs:

```bash
docker logs -f wechat-aiagent
```

or on Fly:

```bash
fly logs
```

## 7. Reverse Proxy and Streaming Notes

Current UI uses standard HTTP POST (no WebSocket requirement). If you later enable streaming:

- Keep `proxy_http_version 1.1`
- Avoid response buffering for stream endpoints (`proxy_buffering off`)
- Increase `proxy_read_timeout` for long responses

## 8. Common Issues

### CORS blocked

- Cause: UI and API are different origins and `WEBUI_CORS_ALLOWED_ORIGINS` is empty.
- Fix: set explicit allowlist domains.

### Public URL opens, but chat fails

- Verify `DASHSCOPE_API_KEY` and `DASHSCOPE_BASE_URL` are set in the runtime environment.
- Check app logs for timeout, quota, or upstream authentication errors.

### WeChat callback works but UI not reachable

- Check reverse proxy routes `/ui/herbal_advice` and `/ui/herbal_advice/api/chat`.
- If you intentionally changed `WEBUI_BASE_PATH`, make sure the proxy matches that exact path.
- Ensure firewall allows ports `80/443` to proxy host.
