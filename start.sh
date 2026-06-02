#!/bin/bash
cd /Users/hazelnut/Desktop/CSB/MyApps/InvestChatBots
source venv/bin/activate
exec python -m uvicorn main:app --host 127.0.0.1 --port 8001
