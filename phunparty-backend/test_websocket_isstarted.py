"""
Test script to verify isstarted field is being sent through WebSocket
"""

import json
from app.database.dbCRUD import get_game_session_state, get_current_question_details
from app.dependencies import get_db


def test_isstarted_in_responses():
    """Test if isstarted is included in WebSocket responses"""

    # Test with a mock session_code
    session_code = "TEST123"

    try:
        db = next(get_db())

        # Test get_game_session_state
        print("=== Testing get_game_session_state ===")
        game_state = get_game_session_state(db, session_code)
        if game_state:
            print(
                f"Game state object has isstarted: {hasattr(game_state, 'isstarted')}"
            )
            if hasattr(game_state, "isstarted"):
                print(f"isstarted value: {game_state.isstarted}")
        else:
            print("No game state found for test session")

        # Test get_current_question_details
        print("\n=== Testing get_current_question_details ===")
        current_question = get_current_question_details(db, session_code)
        print("Current question response:")
        print(json.dumps(current_question, indent=2, default=str))

        if "isstarted" in current_question:
            print(f"✓ isstarted is included: {current_question['isstarted']}")
        else:
            print("✗ isstarted is NOT included")

    except Exception as e:
        print(f"Error during test: {e}")


if __name__ == "__main__":
    test_isstarted_in_responses()
