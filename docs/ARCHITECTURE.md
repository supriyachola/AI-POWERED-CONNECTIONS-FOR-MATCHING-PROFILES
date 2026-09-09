# Affinity Plus — Runnable Architecture

## Product flow

```text
Profile + Match Preferences
        |
        v
POST /me/profile
        |
        v
GET /discover/next
        |
        +--> hard filters: age / gender preference / location preference / blocks
        |
        +--> ranking: TF-IDF + shared interests + vibe + location + age
        |
        v
One person at a time
   |             |
 SKIP          CONNECT
   |             |
history       Connection
                 |
                 v
       WebSocket signaling
                 |
                 v
             WebRTC
          /      |      \
       audio   video    chat

Friends <---- Connections
Recent  <---- InteractionHistory
Blocked <---- Blocks
Reports <---- Reports
```

## Backend modules

- `app/main.py`: HTTP API, matching filters, friends/history/block/report APIs, WebSocket signaling.
- `app/models.py`: User, Profile, Connection, DiscoveryAction, InteractionHistory, Block, Report.
- `app/matching.py`: ranking and explanation logic.
- `app/database.py`: SQLAlchemy engine plus a small migration layer for the current MVP.
- `app/auth.py`: password hashing and JWT.

## Database model

### users
Identity and authentication.

### profiles
Public profile + matching preferences:

- age
- gender
- country
- Indian state/UT
- city
- vibe
- interests
- preferred gender
- preferred country/state
- preferred age range

### connections
The user's friend list. A connection can be `connected`, `removed`, or `blocked`.

### interaction_history
A timeline of user-to-user events such as:

- skipped
- connected
- call ended
- reported
- blocked
- unblocked
- removed

### blocks
Directional user blocks. Matching excludes both sides of a block.

### reports
Moderation reports with reason, details and status.

## Matching strategy

The matcher deliberately uses a hybrid approach instead of making demographics the score itself.

1. **Hard filters** enforce the user's chosen matching boundaries.
2. **Semantic profile similarity** uses TF-IDF/cosine similarity.
3. **Interest overlap** uses Jaccard-style shared-interest scoring.
4. **Vibe** gives a small boost for an explicit shared vibe.
5. **Location** gives a small ranking boost when country/state matches.
6. **Age proximity** gives a small ranking boost.
7. Blocked, already-connected and previously skipped people are excluded.

This keeps age/gender/location as preference controls while interests and vibe remain the main discovery signal.

## API surface

### Profile
- `GET /me`
- `PUT /me/profile`
- `GET /meta/india-states`

### Discovery
- `GET /discover/next`
- `POST /discover/{candidate_id}/skip`

### Friends
- `POST /connections`
- `GET /connections`
- `DELETE /connections/{connection_id}`

### Social safety
- `GET /history`
- `GET /blocked`
- `POST /users/{id}/block`
- `DELETE /users/{id}/block`
- `POST /users/{id}/report`

### Realtime
- `WS /ws/connection/{connection_id}`
- signaling: `offer`, `answer`, `ice-candidate`
- chat: `chat`
- lifecycle: `hangup`, `peer_joined`, `peer_left`

## Deployment

### Frontend
Vercel / Vite / React.

### Backend
Render / FastAPI / Uvicorn.

### Database
Neon PostgreSQL using SQLAlchemy + `psycopg`.

### Realtime media
WebRTC in the browser. The backend is the signaling channel; media is peer-to-peer when possible.

For production reliability, add an authorized TURN service. STUN alone is not sufficient for every network/NAT. WebRTC uses signaling to exchange offer/answer and ICE candidates, and TURN provides a relay when direct peer connectivity cannot be established. See MDN's WebRTC signaling and protocol guidance.

## Scaling path

Current MVP:

```text
Vercel -> Render FastAPI -> Neon
                 |
                 +-> in-memory WebSocket rooms
```

Next production step:

```text
Vercel
  |
API Gateway / Load Balancer
  |
+-------------------------------+
| FastAPI instances             |
| auth / profile / matching API |
+-------------------------------+
       |              |
       |              +--> Redis (presence + matchmaking queue)
       |
       +--> Neon PostgreSQL
       |
       +--> Object storage (only if profile media is introduced)

WebRTC <----> TURN relay when P2P fails
```

When more than one backend instance is used, move WebSocket presence/room state out of process into Redis or a dedicated realtime service. Do not depend on Python process memory for cross-instance rooms.
