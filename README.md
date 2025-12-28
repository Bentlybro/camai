# CAMAI - Jetson AI Camera

Front door AI camera system for Jetson Orin Nano. Detects people, vehicles, and packages using YOLO11 + TensorRT.

## Quick Start

```bash
# Clone
git clone https://github.com/Bentlybro/camai.git
cd camai

# Configure
cp config/.env.example .env
nano .env  # Set your RTSP URL

# Run
./start.sh
```

View stream at: `http://JETSON_IP:8080/stream`

## Requirements

- Jetson Orin Nano with JetPack 6.x
- Docker installed
- RTSP camera on local network

## Features

- **YOLO11n + TensorRT FP16** - Fast inference (~5ms)
- **Person detection** - Alerts when someone dwells at door
- **Vehicle detection** - Alerts when car stops
- **Package detection** - Detects deliveries
- **MJPEG stream** - View annotated video in browser
- **Notifications** - Discord, MQTT, file logging

## Configuration

Edit `.env`:

```env
RTSP_URL=rtsp://user:pass@camera-ip:554/stream1
CONFIDENCE=0.5
PERSON_DWELL_TIME=3.0
ENABLE_DISCORD=false
DISCORD_WEBHOOK=https://discord.com/api/webhooks/...
```

## Updating

```bash
git pull
./start.sh
```

## Project Structure

```
camai/
├── src/           # Python source
├── config/        # Config templates
├── .env           # Your settings (git ignored)
├── start.sh       # Run script
└── run.py         # Entry point
```
