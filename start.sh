#!/bin/sh
set -e
export PYTHONUNBUFFERED=1
cd src
exec python3 main.py
