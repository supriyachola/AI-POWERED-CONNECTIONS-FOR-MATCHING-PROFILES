# server.py
import asyncio
import websockets

# Store connected users
connected_users = set()

async def handler(websocket, path):
    # Add user to connected_users
    connected_users.add(websocket)
    print(f"New connection. Users online: {len(connected_users)}")

    try:
        # Notify user if they are the only one online
        if len(connected_users) == 1:
            await websocket.send("No one is online.")

        # Wait for messages from the user
        async for message in websocket:
            if message == "skip":
                await websocket.send("Skipping to the next user...")
                continue

            # Broadcast the message to all users except the sender
            for user in connected_users:
                if user != websocket:
                    await user.send(message)
    except websockets.ConnectionClosed:
        print("A user disconnected.")
    finally:
        # Remove user from connected_users
        connected_users.remove(websocket)
        print(f"User disconnected. Users online: {len(connected_users)}")

start_server = websockets.serve(handler, "localhost", 8765)

print("Server is running on ws://localhost:8765")
asyncio.get_event_loop().run_until_complete(start_server)
asyncio.get_event_loop().run_forever()