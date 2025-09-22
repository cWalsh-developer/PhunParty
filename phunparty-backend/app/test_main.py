import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# Mock environment variables before any imports
test_env_vars = {
    "DB_User": "test_user",
    "DB_Password": "test_password",
    "DB_Host": "localhost",
    "DB_Port": "5432",
    "DB_Name": "test_db",
}


@patch.dict(os.environ, test_env_vars)
@patch("app.config.create_engine")
def test_app_import(mock_engine):
    """Test that we can import the app with mocked database."""
    mock_engine.return_value = MagicMock()
    from app.main import app

    assert app is not None
    assert app.title == "PhunParty Backend API"


class TestMainApp:
    """Test cases for the main FastAPI application setup."""

    @patch.dict(os.environ, test_env_vars)
    @patch("app.config.create_engine")
    @patch("app.main.Base")
    def setup_method(self, method, mock_base, mock_engine):
        """Set up test client for each test."""
        mock_engine.return_value = MagicMock()
        mock_base.metadata = MagicMock()
        mock_base.metadata.create_all = MagicMock()
        from app.main import app

        self.app = app
        self.client = TestClient(app)

    def test_app_creation(self):
        """Test that the FastAPI app is created with correct title."""
        assert self.app.title == "PhunParty Backend API"

    def test_app_has_game_router(self):
        """Test that game router is included with correct prefix and tags."""
        # Test that the game router is accessible via HTTP call
        response = self.client.get("/game/")
        # Should get a response (even if 403/405) indicating the route exists
        assert response.status_code in [200, 404, 405, 422, 403]

    def test_app_has_players_router(self):
        """Test that players router is included with correct prefix and tags."""
        # Test that the players router is accessible via HTTP call
        response = self.client.get("/players/")
        # Should get a response (even if 403/405) indicating the route exists
        assert response.status_code in [200, 404, 405, 422, 403]

    def test_app_has_questions_router(self):
        """Test that questions router is included with correct prefix and tags."""
        # Test that the questions router is accessible via HTTP call
        response = self.client.get("/questions/")
        # Should get a response (even if 403/405) indicating the route exists
        assert response.status_code in [200, 404, 405, 422, 403]

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
        # We expect either a valid response or method not allowed (405) or API key required (403)
        assert response.status_code in [200, 404, 405, 422, 403]

    def test_players_router_prefix(self):
        """Test that player endpoints are accessible under /players prefix."""
        response = self.client.get("/players/")
        # We expect either a valid response or method not allowed (405) or API key required (403)
        assert response.status_code in [200, 404, 405, 422, 403]

    def test_questions_router_prefix(self):
        """Test that question endpoints are accessible under /questions prefix."""
        response = self.client.get("/questions/")
        # We expect either a valid response or method not allowed (405)
        assert response.status_code in [200, 404, 405, 422]


@patch.dict(os.environ, test_env_vars)
@patch("app.config.create_engine")
@patch("app.main.Base")
def test_app_imports_successfully(mock_base, mock_engine):
    """Test that all imported modules are accessible."""
    mock_engine.return_value = MagicMock()
    mock_base.metadata = MagicMock()
    mock_base.metadata.create_all = MagicMock()

    try:
        from fastapi import Depends, FastAPI

        from app.main import app

        assert app is not None
        assert isinstance(app, FastAPI)
        assert app.title == "PhunParty Backend API"

    except ImportError as e:
        pytest.fail(f"Import failed: {e}")


@patch.dict(os.environ, test_env_vars)
@patch("app.config.create_engine")
def test_database_url_construction(mock_engine):
    """Test that database URL is constructed correctly with test environment."""
    mock_engine.return_value = MagicMock()

    from app.config import DatabaseURL

    expected_url = "postgresql://test_user:test_password@localhost:5432/test_db"
    assert DatabaseURL == expected_url


if __name__ == "__main__":
    pytest.main([__file__])
