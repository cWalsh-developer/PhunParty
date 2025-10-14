"""
Debug script to test automatic game advancement
This will help us understand what's happening when players submit answers
"""

import asyncio
import json
import websockets
import time

async def simulate_player_answer(session_code, player_id, question_id, answer):
    """Simulate a player submitting an answer via WebSocket"""
    uri = f"ws://localhost:8000/ws/{session_code}"
    
    try:
        async with websockets.connect(uri) as websocket:
            # Register as mobile client
            register_message = {
                "type": "register_client",
                "data": {
                    "client_type": "mobile",
                    "player_id": player_id
                }
            }
            await websocket.send(json.dumps(register_message))
            
            # Wait a bit for registration
            await asyncio.sleep(0.5)
            
            # Submit answer
            answer_message = {
                "type": "player_answer",
                "data": {
                    "answer": answer,
                    "question_id": question_id,
                    "player_id": player_id
                }
            }
            
            print(f"Player {player_id} submitting answer: {answer}")
            await websocket.send(json.dumps(answer_message))
            
            # Listen for responses
            try:
                response = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                print(f"Player {player_id} received response: {response}")
            except asyncio.TimeoutError:
                print(f"Player {player_id} - no response received")
                
    except Exception as e:
        print(f"Error for player {player_id}: {e}")

async def listen_to_session(session_code):
    """Listen to all messages for a session"""
    uri = f"ws://localhost:8000/ws/{session_code}"
    
    try:
        async with websockets.connect(uri) as websocket:
            # Register as web client
            register_message = {
                "type": "register_client",
                "data": {
                    "client_type": "web"
                }
            }
            await websocket.send(json.dumps(register_message))
            
            print("Listening for session messages...")
            
            while True:
                try:
                    message = await asyncio.wait_for(websocket.recv(), timeout=30.0)
                    parsed_message = json.loads(message)
                    print(f"SESSION MESSAGE: {parsed_message['type']} - {json.dumps(parsed_message, indent=2)}")
                except asyncio.TimeoutError:
                    print("No messages received in 30 seconds")
                    break
                except Exception as e:
                    print(f"Error listening: {e}")
                    break
                    
    except Exception as e:
        print(f"Error connecting listener: {e}")

async def main():
    """Main test function"""
    session_code = "TEST123"  # Change this to your actual session code
    question_id = "your-question-id"  # Change this to an actual question ID
    
    print(f"Testing automatic advancement for session: {session_code}")
    print("Make sure you have:")
    print("1. A game session created with the session code")
    print("2. Players joined to the session")
    print("3. Game started")
    print("4. Update the session_code and question_id variables above")
    print("")
    
    # Start listener task
    listener_task = asyncio.create_task(listen_to_session(session_code))
    
    # Wait a bit for listener to connect
    await asyncio.sleep(1)
    
    # Simulate multiple players answering - you'll need to update these with real player IDs
    players = [
        ("player1", "A"),  # (player_id, answer)
        ("player2", "B"),
        ("player3", "A")
    ]
    
    # Submit answers with delays
    for player_id, answer in players:
        await simulate_player_answer(session_code, player_id, question_id, answer)
        await asyncio.sleep(2)  # Wait 2 seconds between submissions
    
    # Let listener run for a bit more to catch any delayed messages
    await asyncio.sleep(10)
    
    # Cancel listener
    listener_task.cancel()
    
    print("Test complete!")

if __name__ == "__main__":
    asyncio.run(main())