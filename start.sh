#!/bin/bash
cd /Users/cheonseungbeom/Desktop/CSB/MyApps/InvestChatBots
source venv/bin/activate
exec uvicorn main:app --host 127.0.0.1 --port 8001
