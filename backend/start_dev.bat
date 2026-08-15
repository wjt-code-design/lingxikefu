@echo off
cd /d %~dp0
REM 覆盖 Windows 用户环境变量里的脏占位符 ZHIPU_API_KEY=你的Key。
REM pydantic-settings 优先级: 环境变量 > .env，脏值会无声盖掉 .env 真实 key。
REM 这里显式设成真实 key，不依赖也不修改系统环境变量。
set "ZHIPU_API_KEY=[REDACTED-ZHIPU-KEY]"
REM 宿主机直接跑时 docker 服务名(postgres/qdrant)解析不了，改回 localhost
set "POSTGRES_HOST=localhost"
set "REDIS_URL=redis://localhost:6379/0"
set "QDRANT_URL=http://localhost:6333"
uvicorn app.main:app --host 0.0.0.0 --port 8003 --reload
