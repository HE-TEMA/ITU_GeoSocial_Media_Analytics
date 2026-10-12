#!/bin/bash

## NGROK startup:
# echo "launching ngrok"> startup.log &
 
 ## write to log -- ngrok startup
# Add the ngrok auth token
# ngrok config add-authtoken $NGROK_TOKEN

# Start ngrok in the background and log output to stdout
# ngrok http --url=$BASE_NGROK_URL $PORT --log=stdout > ngrok.log &
# echo "launching app.py"> startup.log &

## general app startup:
cd /component/app
SCRIPT_NAME=$BASE_PATH gunicorn -w 1 'app:app' # --error-logfile /component/app/gunicorn.stdout.log --capture-output --log-level debug
#python3 ./app/app.py