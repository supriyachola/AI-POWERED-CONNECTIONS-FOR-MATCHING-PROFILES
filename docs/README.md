# Affinity Plus

Affinity Plus is an AI-powered social discovery platform that introduces people one at a time based on interests, vibe, age/gender preferences and location, then lets connected users move directly into live audio/video and chat.

## Stack

- React + Vite + TypeScript
- FastAPI + SQLAlchemy
- PostgreSQL / Neon in production
- JWT authentication
- TF-IDF + cosine similarity + shared-interest/vibe ranking
- WebSocket signaling
- WebRTC audio/video

## Run backend

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload
```

## Run frontend

```bash
cd frontend
npm install
npm run dev
```

Set the frontend API base in `src/App.tsx` for a different backend environment.

## Production database

Use:

```text
postgresql+psycopg://...
```

not plain `postgresql://`, because this project uses the psycopg v3 driver.

## Current feature set

- guest + registered accounts
- age/gender/location profile
- Indian state/UT selection
- gender + age + location matching preferences
- interest + vibe based ranking
- one-person-at-a-time discovery
- skip history
- friends list
- recent activity history
- remove friend
- block/unblock
- report user
- live audio/video
- real-time chat
- WebSocket/WebRTC signaling

See `ARCHITECTURE.md` for the system design and `FEATURES.md` for the product contract.
