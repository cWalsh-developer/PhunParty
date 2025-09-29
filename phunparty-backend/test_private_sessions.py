"""
Simple test script to verify get_player_private_sessions function
"""
import os
from unittest.mock import MagicMock, patch

# Mock environment variables
test_env_vars = {
    "DB_User": "test_user",
    "DB_Password": "test_password",
    "DB_Host": "localhost",
    "DB_Port": "5432",
    "DB_Name": "test_db",
}

@patch.dict(os.environ, test_env_vars)
@patch("app.config.create_engine")
def test_get_player_private_sessions_structure(mock_engine):
    """Test that get_player_private_sessions returns expected structure"""
    # Mock database and session
    mock_engine.return_value = MagicMock()
    mock_db = MagicMock()

    # Import after mocking
    from app.database.dbCRUD import get_player_private_sessions

    # Mock query chain
    mock_query = MagicMock()
    mock_db.query.return_value = mock_query
    mock_query.join.return_value = mock_query
    mock_query.filter.return_value = mock_query

    # Mock result data
    mock_session = MagicMock()
    mock_session.session_code = "TEST123"
    mock_session.number_of_questions = 10

    mock_game = MagicMock()
    mock_game.genre = "Science"

    mock_state = MagicMock()
    mock_state.ispublic = False

    mock_query.all.return_value = [(mock_session, mock_game, mock_state)]

    # Mock get_session_difficulty
    with patch('app.database.dbCRUD.get_session_difficulty', return_value="Easy"):
        result = get_player_private_sessions(mock_db, "player123")

    # Verify structure
    assert isinstance(result, list)
    assert len(result) == 1

    session_data = result[0]
    expected_keys = {"session_code", "genre", "number_of_questions", "difficulty", "ispublic"}
    assert set(session_data.keys()) == expected_keys

    # Verify values
    assert session_data["session_code"] == "TEST123"
    assert session_data["genre"] == "Science"
    assert session_data["number_of_questions"] == 10
    assert session_data["difficulty"] == "Easy"
    assert session_data["ispublic"] == False

    print("Function returns expected structure")
    print(f"Sample result: {result}")

if __name__ == "__main__":
    test_get_player_private_sessions_structure()