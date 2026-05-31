#!/bin/bash
cd /Users/hazelnut/CSB/MyApps/InvestChatBots
source venv/bin/activate
exec uvicorn main:app --host 127.0.0.1 --port 8001
