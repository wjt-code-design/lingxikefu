@echo off
cd /d %~dp0
REM 从 .env 读取真实 LONGCAT_API_KEY 注入（覆盖 Windows 用户环境变量里的脏占位符，
REM 避免 pydantic-settings 的"环境变量 > .env"优先级让脏值无声盖掉 .env 真实 key；
REM key 只存 .env（已被 .gitignore 双保险忽略），此处不硬编码任何密钥）。
for /f "usebackq tokens=1,* delims==" %%a in ("%~dp0.env") do (
  if /i "%%a"=="LONGCAT_API_KEY" set "LONGCAT_API_KEY=%%b"
)
REM 宿主机直接跑时 docker 服务名(postgres/qdrant)解析不了，改回 localhost
set "POSTGRES_HOST=localhost"
set "REDIS_URL=redis://localhost:6379/0"
set "QDRANT_URL=http://localhost:6333"
uvicorn app.main:app --host 0.0.0.0 --port 8003 --reload
