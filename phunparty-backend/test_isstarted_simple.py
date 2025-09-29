from app.database.dbCRUD import get_current_question_details, get_game_session_state
from app.dependencies import get_db
import json
from sqlalchemy import text

def test_isstarted():
    db = next(get_db())
    try:
        # Get any existing session
        result = db.execute(text('SELECT session_code FROM game_session_states LIMIT 1'))
        session = result.fetchone()
        
        if session:
            session_code = session[0]
            print(f'Testing with session: {session_code}')
            
            # Test get_current_question_details
            details = get_current_question_details(db, session_code)
            print('Current question details:')
            print(json.dumps(details, indent=2, default=str))
            
            if 'isstarted' in details:
                print(f'✓ isstarted is included: {details["isstarted"]}')
            else:
                print('✗ isstarted is NOT included')
                
            # Also test the raw game state
            print('\n=== Raw game state object ===')
            game_state = get_game_session_state(db, session_code)
            if game_state:
                print(f'isstarted attribute: {hasattr(game_state, "isstarted")}')
                if hasattr(game_state, 'isstarted'):
                    print(f'isstarted value: {game_state.isstarted}')
        else:
            print('No existing sessions found in database')
            
    except Exception as e:
        print(f'Error: {e}')
        import traceback
        traceback.print_exc()
    finally:
        db.close()

if __name__ == "__main__":
    test_isstarted()