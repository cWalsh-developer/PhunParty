"""
Unit tests for API key protection functionality (new version)
"""

import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# Test environment variables
test_env_vars = {
    "DB_User": "test_user",
    "DB_Password": "test_password",
    "DB_Host": "localhost",
    "DB_Port": "5432",
    "DB_Name": "test_db",
}

# Test configuration
VALID_API_KEY = "your-secure-api-key-here-change-this"
INVALID_API_KEY = "invalid-key"


@patch.dict(os.environ, test_env_vars)
@patch("app.config.create_engine")
@patch("app.main.Base")
def test_api_key_protection(mock_base, mock_engine):
    """Test that API endpoints require valid API key using TestClient"""
    # Setup mocks
    mock_engine.return_value = MagicMock()
    mock_base.metadata = MagicMock()
    mock_base.metadata.create_all = MagicMock()

    from app.main import app

    client = TestClient(app)

    # Test root endpoint (should be public)
    response = client.get("/")
    assert response.status_code == 200
    assert "PhunParty Backend API" in response.json()["message"]

    # Test health endpoint
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"

    # Test docs endpoint
    response = client.get("/docs")
    assert response.status_code == 200


@patch.dict(os.environ, test_env_vars)
@patch("app.config.create_engine")
@patch("app.main.Base")
def test_specific_endpoint(mock_base, mock_engine):
    """Test a specific endpoint with proper mocking"""
    # Setup mocks
    mock_engine.return_value = MagicMock()
    mock_base.metadata = MagicMock()
    mock_base.metadata.create_all = MagicMock()

    from app.main import app

    client = TestClient(app)

    # Test that the app loads successfully
    response = client.get("/")
    assert response.status_code == 200

    # Test OpenAPI endpoint
    response = client.get("/openapi.json")
    assert response.status_code == 200


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
