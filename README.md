# Windows Agent

A local AI agent that automates data entry tasks via OS-level mouse and keyboard control.

## Architecture

```
windows-agent/
├── backend/          # Python FastAPI server
│   ├── main.py       # API routes, WebSocket screen feed, action execution
│   └── requirements.txt
└── frontend/         # React + Mantine + Vite
    ├── src/
    │   ├── App.jsx
    │   └── components/
    │       └── InterventionDashboard.jsx
    └── package.json
```

## Tech Stack

| Layer           | Technology                         |
| --------------- | ---------------------------------- |
| Frontend        | React, Mantine, Vite               |
| Backend         | Python, FastAPI                    |
| OS Control      | PyAutoGUI (mouse/keyboard)         |
| Screen Capture  | mss (fast screenshots)             |
| Communication   | REST API + WebSocket               |

## Getting Started

### Prerequisites

- Python 3.10+
- Node.js 18+

### Backend

```bash
cd backend
python -m venv venv
source venv/bin/activate   # On Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Windows PowerShell example:

```powershell
cd backend
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

The API will be available at `http://localhost:8000`.

### Frontend

```bash
npm install
npm run dev
```

Optional: set `VITE_API_BASE` in `frontend/.env` if your backend is not running
on `http://localhost:8000`.

The dev server will start at `http://localhost:5173`.

## API Reference

| Method | Endpoint         | Description                                    |
| ------ | ---------------- | ---------------------------------------------- |
| GET    | `/api/status`    | Returns the agent status (idle / running / requires_intervention) |
| POST   | `/api/intervene` | Accepts `{ x, y }` to move & click the mouse   |
| WS     | `/ws/screen`     | Streams base64 PNG screenshots at ~3 FPS        |
