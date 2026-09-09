# Affinity Plus — Social V5

Affinity Plus is an 18+ AI-powered social discovery app. It shows one potential connection at a time, prioritizes live users, and supports live WebRTC audio/video plus private real-time chat.

## V5 product behavior

- Minimal signup: username + password + age + gender.
- City is auto-detected after sign-in using browser geolocation → reverse geocoding, with IP lookup as fallback.
- Manual location is available only as a last resort and later as an Edit Profile override.
- Age is collected as a real number (18+), displayed in familiar brackets:
  `18–24`, `25–34`, `35–44`, `45–59`, `60+`.
- Matching uses a rolling real-age window of approximately `age - 3` through `age + 2`, with the candidate's window also checked. Brackets never create hard matching walls.
- "Who do you want to connect with today?" is a per-session picker: Men / Women / Anyone.
- Starting a call does not automatically add a friend.
- In-call `+ Add friend` is an explicit action.
- Friends and History are separate. History contains people actually called, not skips or reports.
- History supports Reconnect when the person is online, or Add Friend when they are offline and not already a friend.
- Online presence, online-first ranking, 3-second auto-connect, chat auto-scroll, emoji picker, responsive UI, block and report remain enabled.

## Production deployment

### Frontend (Vercel)

Root Directory:
`AI-POWERED-CONNECTIONS-FOR-MATCHING-PROFILES-main/frontend`

Build:
`npm run build`

Output:
`dist`

Environment:
`VITE_API_URL=https://affinityplus-api.onrender.com`

### Backend (Render)

Root Directory:
`AI-POWERED-CONNECTIONS-FOR-MATCHING-PROFILES-main/backend`

Build:
`pip install -r requirements.txt`

Start:
`uvicorn app.main:app --host 0.0.0.0 --port $PORT`

Environment:
`DATABASE_URL=postgresql+psycopg://...`
`JWT_SECRET=<long-random-secret>`
`CORS_ORIGINS=https://affinityplus.vercel.app,http://localhost:5173`

### Database

Neon PostgreSQL is supported. The backend performs the existing MVP schema migration on startup.

## WebRTC note

The app includes STUN signaling for normal peer-to-peer connections. For broad production reliability across restrictive NAT/firewall networks, configure an authorized TURN server in the WebRTC configuration before large-scale public launch.
