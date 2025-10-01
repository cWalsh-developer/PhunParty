"""
Example Game Flow - How to use the new game logic system

This demonstrates the automatic game progression workflow:
1. Create a game session (automatically initializes game state)
2. Players join the session
3. Players submit answers (game auto-advances when all answer)
4. Game ends when all questions are answered
"""

# Example API calls and workflow:

# 1. CREATE A GAME SESSION
# POST /game/create/session
# Body: {
#   "host_name": "John",
#   "number_of_questions": 3,
#   "game_code": "TRIVIA001"
# }
# Response: { "session_code": "ABC123XYZ", ... }

# 2. PLAYERS JOIN THE SESSION
# POST /game/join
# Body: {
#   "session_code": "ABC123XYZ",
#   "player_id": "PLAYER001"
# }

# 3. GET CURRENT QUESTION
# GET /game-logic/current-question/ABC123XYZ
# Response: {
#   "question_id": "Q001",
#   "question": "What is the capital of France?",
#   "genre": "Trivia",
#   "question_index": 0,
#   "total_questions": 3,
#   "is_waiting_for_players": true
# }

# 4. PLAYERS SUBMIT ANSWERS
# POST /game-logic/submit-answer
# Body: {
#   "session_code": "ABC123XYZ",
#   "player_id": "PLAYER001",
#   "question_id": "Q001",
#   "player_answer": "Paris"
# }
# Response: {
#   "player_answer": "Paris",
#   "is_correct": true,
#   "game_state": {
#     "players_total": 2,
#     "players_answered": 1,
#     "waiting_for_players": true,
#     "current_question_index": 0,
#     "total_questions": 3,
#     "game_state": "active"
#   }
# }

# 5. WHEN ALL PLAYERS ANSWER - GAME AUTO-ADVANCES
# POST /game-logic/submit-answer (last player)
# Response: {
#   "player_answer": "Paris",
#   "is_correct": true,
#   "game_state": {
#     "players_total": 2,
#     "players_answered": 2,
#     "waiting_for_players": true,  # Reset for next question
#     "current_question_index": 1,  # Advanced to next question
#     "total_questions": 3,
#     "game_state": "active",
#     "action": "next_question",
#     "next_question_id": "Q002"
#   }
# }

# 6. CHECK GAME STATUS ANYTIME
# GET /game-logic/status/ABC123XYZ
# Response: {
#   "session_code": "ABC123XYZ",
#   "is_active": true,
#   "is_waiting_for_players": true,
#   "current_question_index": 1,
#   "total_questions": 3,
#   "current_question": {
#     "question_id": "Q002",
#     "question": "Which planet is known as the Red Planet?",
#     "genre": "Science"
#   },
#   "players": {
#     "total": 2,
#     "answered": 0,
#     "waiting_for": 2
#   }
# }

# 7. WHEN LAST QUESTION IS ANSWERED - GAME ENDS
# POST /game-logic/submit-answer (final answer)
# Response: {
#   "player_answer": "Mars",
#   "is_correct": true,
#   "game_state": {
#     "action": "game_ended",
#     "game_state": "completed",
#     "final_results": [
#       {
#         "player_id": "PLAYER001",
#         "score": 3,
#         "result": "win"
#       },
#       {
#         "player_id": "PLAYER002",
#         "score": 2,
#         "result": "lose"
#       }
#     ]
#   }
# }
