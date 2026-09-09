# Affinity Plus — Feature Contract

## 1. Profile

Every profile can contain:

- age (18+)
- gender
- country
- city
- Indian state/UT when country is India
- bio
- looking for
- interests
- vibe

## 2. Match preferences

Before discovery the user can choose:

- gender: Anyone (both), Women, Men, Non-binary
- age range
- location: Anywhere or a chosen country
- Indian state when India is selected

The backend applies these filters and also respects the other user's preferences.

## 3. Discovery

The user sees one person at a time.

- `FIND MY MATCH` starts discovery.
- `SKIP` records a skip and permanently removes that candidate from the current discovery pool.
- `START VIDEO + AUDIO` creates/opens a connection.

## 4. Matching

Primary signals:

- shared interests
- semantic profile overlap
- shared vibe

Secondary signals:

- same country/state
- age proximity

Safety constraints:

- blocks
- previous connections
- previous skips
- self-match prevention

## 5. Friends

The `Friends` drawer shows connected users and allows:

- open live room
- remove friend
- block
- report

## 6. Recent

The `Recent` tab shows the user's recent interaction history, including skips, connections, call ends, reports, blocks and removals.

## 7. Block

A block:

- prevents future discovery
- prevents a new connection
- prevents WebRTC room access
- hides the person from Friends

## 8. Report

A report stores:

- reporter
- reported user
- reason
- optional details
- moderation status
- timestamp

The MVP records the report for a future moderation dashboard.

## 9. Live communication

The existing WebRTC call room supports:

- microphone toggle
- camera toggle
- end call
- real-time chat
- WebSocket signaling

Production should add TURN and a persistent/observable presence layer before large-scale public launch.
