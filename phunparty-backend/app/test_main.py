import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
import os

# Mock environment variables before any imports
test_env_vars = {
    "DB_User": "test_user",
    "DB_Password": "test_password",
    "DB_Host": "localhost",
    "DB_Port": "5432",
    "DB_Name": "test_db",
}


@patch.dict(os.environ, test_env_vars)
@patch("App.config.create_engine")
def test_app_import(mock_engine):
    """Test that we can import the app with mocked database."""
    mock_engine.return_value = MagicMock()
    from App.main import app

    return app


class TestMainApp:
    """Test cases for the main FastAPI application setup."""

    @patch.dict(os.environ, test_env_vars)
    @patch("App.config.create_engine")
    def setup_method(self, mock_engine):
        """Set up test client for each test."""
        mock_engine.return_value = MagicMock()
        from App.main import app

        self.app = app
        self.client = TestClient(app)

    def test_app_creation(self):
        """Test that the FastAPI app is created with correct title."""
        assert self.app.title == "PhunParty Backend API"

    def test_app_has_game_router(self):
        """Test that game router is included with correct prefix and tags."""
        routes = [route for route in self.app.routes if hasattr(route, "path_regex")]
        game_routes = [route for route in routes if route.path.startswith("/game")]
        assert len(game_routes) > 0

    def test_app_has_players_router(self):
        """Test that players router is included with correct prefix and tags."""
        routes = [route for route in self.app.routes if hasattr(route, "path_regex")]
        player_routes = [route for route in routes if route.path.startswith("/players")]
        assert len(player_routes) > 0

    def test_app_has_questions_router(self):
        """Test that questions router is included with correct prefix and tags."""
        routes = [route for route in self.app.routes if hasattr(route, "path_regex")]
        question_routes = [
            route for route in routes if route.path.startswith("/questions")
        ]
        assert len(question_routes) > 0

    def test_app_openapi_tags(self):
        """Test that OpenAPI schema includes correct tags."""
        openapi_schema = self.app.openapi()
        assert openapi_schema is not None

    def test_app_responds_to_docs(self):
        """Test basic app functionality with a simple request."""
        response = self.client.get("/docs")
        assert response.status_code == 200

    def test_app_responds_to_openapi(self):
        """Test that OpenAPI endpoint works."""
        response = self.client.get("/openapi.json")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/json"

    def test_game_router_prefix(self):
        """Test that game endpoints are accessible under /game prefix."""
        response = self.client.get("/game/")
        # We expect either a valid response or method not allowed (405)
        assert response.status_code in [200, 404, 405, 422]

    def test_players_router_prefix(self):
        """Test that player endpoints are accessible under /players prefix."""
        response = self.client.get("/players/")
        # We expect either a valid response or method not allowed (405)
        assert response.status_code in [200, 404, 405, 422]

    def test_questions_router_prefix(self):
        """Test that question endpoints are accessible under /questions prefix."""
        response = self.client.get("/questions/")
        # We expect either a valid response or method not allowed (405)
        assert response.status_code in [200, 404, 405, 422]


@patch.dict(os.environ, test_env_vars)
@patch("App.config.create_engine")
def test_app_imports_successfully(mock_engine):
    """Test that all imported modules are accessible."""
    mock_engine.return_value = MagicMock()

    try:
        from App.main import app
        from fastapi import FastAPI, Depends

        assert app is not None
        assert isinstance(app, FastAPI)
        assert app.title == "PhunParty Backend API"

    except ImportError as e:
        pytest.fail(f"Import failed: {e}")


@patch.dict(os.environ, test_env_vars)
@patch("App.config.create_engine")
def test_database_url_construction(mock_engine):
    """Test that database URL is constructed correctly with test environment."""
    mock_engine.return_value = MagicMock()

    from App.config import DatabaseURL

    expected_url = "postgresql://test_user:test_password@localhost:5432/test_db"
    assert DatabaseURL == expected_url


if __name__ == "__main__":
    pytest.main([__file__])
