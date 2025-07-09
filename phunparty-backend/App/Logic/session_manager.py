import uuid

class SessionManager:
    def __init__(self):
        self.sessions = {}

    def create_session(self, host_name: str, players: list, scores: dict) -> str:
        session_id = str(uuid.uuid4())[:6].upper() # Generate a unique session ID`
        self.sessions[session_id] = {"host": host_name,
                                     "players": players,
                                     "scores": scores} # Initialize session with host, players, and scores
        return session_id # Return the session ID
    
    def get_session(self, game_code: str):
        return self.sessions.get(game_code)
    
    def get_all_sessions(self):
        return self.sessions
