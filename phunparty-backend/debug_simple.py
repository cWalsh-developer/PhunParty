"""
Simple debug script to test the game advancement logic directly
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy.orm import sessionmaker
from app.config import engine
from app.logic.game_logic import check_and_advance_game, submit_player_answer, get_question_with_randomized_options
from app.database.dbCRUD import get_game_session_state, get_number_of_players_in_session, count_responses_for_question, get_question_by_id

# Use the existing engine from config
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def test_advancement():
    """Test the advancement logic"""
    db = SessionLocal()
    
    try:
        # First, let's see what sessions exist
        print("Checking for active game sessions...")
        
        # Import models to query directly
        from app.models.game_state_models import GameSessionState
        
        # List all game states
        game_states = db.query(GameSessionState).all()
        print(f"Found {len(game_states)} game states:")
        for state in game_states:
            print(f"  - {state.session_code} (active: {state.is_active}, started: {state.isstarted})")
        
        if not game_states:
            print("No game states found in database")
            return
            
        # Use the first active session, or just the first session if none are active
        active_states = [s for s in game_states if s.is_active and s.isstarted]
        if active_states:
            session_code = active_states[0].session_code
            print(f"Using active started session: {session_code}")
        elif game_states:
            session_code = game_states[0].session_code
            print(f"No active started sessions, using: {session_code}")
        else:
            print("No sessions available")
            return
        
        # Check if session exists
        game_state = get_game_session_state(db, session_code)
        if not game_state:
            print(f"No game session found with code: {session_code}")
            return
            
        print(f"Found session: {session_code}")
        print(f"Current question index: {game_state.current_question_index}")
        print(f"Current question ID: {game_state.current_question_id}")
        print(f"Total questions: {game_state.total_questions}")
        print(f"Is active: {game_state.is_active}")
        print(f"Is waiting for players: {game_state.is_waiting_for_players}")
        
        # Check player counts
        total_players = get_number_of_players_in_session(db, session_code)
        print(f"Total players in session: {total_players}")
        
        if game_state.current_question_id:
            responses = count_responses_for_question(db, session_code, game_state.current_question_id)
            print(f"Responses to current question: {responses}")
            
            # Test the advancement logic
            print("\nTesting check_and_advance_game...")
            result = check_and_advance_game(db, session_code, game_state.current_question_id)
            print(f"Advancement result: {result}")
            
            # Test the question randomization logic
            print(f"\nTesting get_question_with_randomized_options for Q001...")
            try:
                q001_result = get_question_with_randomized_options(db, "Q001")
                print(f"Q001 randomized result: {q001_result}")
            except Exception as e:
                print(f"Error getting Q001: {e}")
                
            # Test getting question by ID directly
            print(f"\nTesting get_question_by_id for Q001...")
            try:
                q001_direct = get_question_by_id("Q001", db)
                if q001_direct:
                    print(f"Q001 direct result:")
                    print(f"  - ID: {q001_direct.question_id}")
                    print(f"  - Question: {q001_direct.question}")
                    print(f"  - Answer: {q001_direct.answer}")
                    print(f"  - Options (raw): {repr(q001_direct.question_options)}")
                else:
                    print("Q001 not found")
            except Exception as e:
                print(f"Error getting Q001 directly: {e}")
        else:
            print("No current question set")
            
    except Exception as e:
        print(f"Error: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    test_advancement()