import pytest
from fastapi.testclient import TestClient
from App.main import app

client = TestClient(app)


def test_read_root():
    """Test that the API is running and responds"""
    response = client.get("/")
    # FastAPI returns 404 for undefined routes, which means the app is working
    assert response.status_code in [200, 404]


def test_docs_endpoint():
    """Test that the API docs are accessible"""
    response = client.get("/docs")
    assert response.status_code == 200
    assert "swagger" in response.text.lower() or "openapi" in response.text.lower()


def test_openapi_endpoint():
    """Test that the OpenAPI schema is accessible"""
    response = client.get("/openapi.json")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"


def test_game_router_included():
    """Test that game router endpoints exist (even if they return errors)"""
    response = client.get("/game/")
    # Should not return 404 (route not found), any other status means the route exists
    assert response.status_code != 404


def test_players_router_included():
    """Test that players router endpoints exist (even if they return errors)"""
    response = client.get("/players/")
    # Should not return 404 (route not found), any other status means the route exists
    assert response.status_code != 404


def test_questions_router_included():
    """Test that questions router endpoints exist (even if they return errors)"""
    response = client.get("/questions/")
    # Should not return 404 (route not found), any other status means the route exists
    assert response.status_code != 404


def test_app_title():
    """Test that the app has the correct title"""
    response = client.get("/openapi.json")
    openapi_schema = response.json()
    assert openapi_schema["info"]["title"] == "PhunParty Backend API"


if __name__ == "__main__":
    pytest.main([__file__])
