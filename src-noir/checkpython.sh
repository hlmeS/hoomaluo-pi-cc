#!/bin/bash
# Basic if statement
if ! pgrep -f app.py
then
cd ~/hoomaluo-pi-cc/src-noir
python3 app.py > test.out &
fi
