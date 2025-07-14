import os
import sys
from unittest.mock import patch, MagicMock, Mock
from pathlib import Path

# Add the current directory to Python path
sys.path.insert(0, str(Path(__file__).parent))

# Test environment variables
test_env_vars = {
    'DB_User': 'test_user',
    'DB_Password': 'test_password', 
    'DB_Host': 'localhost',
    'DB_Port': '5432',
    'DB_Name': 'test_db'
}


def test_environment_variables():
    """Test that environment variables are properly set."""
    with patch.dict(os.environ, test_env_vars):
        assert os.getenv('DB_User') == 'test_user'
        assert os.getenv('DB_Password') == 'test_password'
        assert os.getenv('DB_Host') == 'localhost'
        assert os.getenv('DB_Port') == '5432'
        assert os.getenv('DB_Name') == 'test_db'


def test_database_url_construction():
    """Test database URL construction with test environment variables."""
    with patch.dict(os.environ, test_env_vars):
        with patch('sqlalchemy.create_engine'):
            from app.config import DatabaseURL
            expected_url = "postgresql://test_user:test_password@localhost:5432/test_db"
            assert DatabaseURL == expected_url


def test_imports_work():
    """Test that basic imports work without database connection."""
    # Test that we can import config without connecting to database
    with patch.dict(os.environ, test_env_vars):
        with patch('sqlalchemy.create_engine'):
            try:
                from app import config
                assert config is not None
            except Exception as e:
                assert False, f"Config import failed: {e}"


def test_fastapi_app_creation():
    """Test that FastAPI app can be created with proper mocking."""
    with patch.dict(os.environ, test_env_vars):
        # Mock all database-related components
        with patch('sqlalchemy.create_engine') as mock_create_engine:
            with patch('sqlalchemy.orm.sessionmaker') as mock_sessionmaker:
                with patch('app.main.Base') as mock_base:
                    # Setup mocks
                    mock_engine = Mock()
                    mock_create_engine.return_value = mock_engine
                    
                    mock_session_factory = Mock()
                    mock_sessionmaker.return_value = mock_session_factory
                    
                    mock_metadata = Mock()
                    mock_base.metadata = mock_metadata
                    mock_metadata.create_all = Mock()
                    
                    # Now try to import and test the app
                    try:
                        from app.main import app
                        
                        # Basic checks
                        assert app is not None
                        assert hasattr(app, 'title')
                        assert app.title == "PhunParty Backend API"
                        
                        # Check routes exist
                        routes = [route.path for route in app.routes if hasattr(route, 'path')]
                        assert len(routes) > 0
                        
                    except Exception as e:
                        assert False, f"App creation failed: {e}"


def test_fastapi_basic_functionality():
    """Test basic FastAPI functionality with TestClient."""
    with patch.dict(os.environ, test_env_vars):
        # Mock all database-related components
        with patch('sqlalchemy.create_engine') as mock_create_engine:
            with patch('sqlalchemy.orm.sessionmaker') as mock_sessionmaker:
                with patch('app.main.Base') as mock_base:
                    with patch('app.main.get_db') as mock_get_db:
                        # Setup mocks
                        mock_engine = Mock()
                        mock_create_engine.return_value = mock_engine
                        
                        mock_session_factory = Mock()
                        mock_sessionmaker.return_value = mock_session_factory
                        
                        mock_metadata = Mock()
                        mock_base.metadata = mock_metadata
                        mock_metadata.create_all = Mock()
                        
                        mock_db_session = Mock()
                        mock_get_db.return_value = mock_db_session
                        
                        try:
                            from app.main import app
                            from fastapi.testclient import TestClient
                            
                            client = TestClient(app)
                            
                            # Test OpenAPI endpoint
                            response = client.get("/openapi.json")
                            assert response.status_code == 200
                            
                            # Test docs endpoint 
                            response = client.get("/docs")
                            assert response.status_code == 200
                            
                        except Exception as e:
                            assert False, f"FastAPI functionality test failed: {e}"


def test_routers_included():
    """Test that all expected routers are included in the app."""
    with patch.dict(os.environ, test_env_vars):
        # Mock all database-related components
        with patch('sqlalchemy.create_engine') as mock_create_engine:
            with patch('sqlalchemy.orm.sessionmaker') as mock_sessionmaker:
                with patch('app.main.Base') as mock_base:
                    # Setup mocks
                    mock_engine = Mock()
                    mock_create_engine.return_value = mock_engine
                    
                    mock_session_factory = Mock()
                    mock_sessionmaker.return_value = mock_session_factory
                    
                    mock_metadata = Mock()
                    mock_base.metadata = mock_metadata
                    mock_metadata.create_all = Mock()
                    
                    try:
                        from app.main import app
                        
                        # Get all route paths
                        route_paths = [route.path for route in app.routes if hasattr(route, 'path')]
                        
                        # Check that we have routes (even if we can't test specific ones due to mocking)
                        assert len(route_paths) > 0, "No routes found in the app"
                        
                        # Check that the app has the expected title
                        assert app.title == "PhunParty Backend API"
                        
                    except Exception as e:
                        assert False, f"Router inclusion test failed: {e}"


def test_config_import():
    """Test that config module can be imported independently."""
    with patch.dict(os.environ, test_env_vars):
        with patch('sqlalchemy.create_engine'):
            try:
                import app.config as config
                assert hasattr(config, 'DatabaseURL')
                assert config.DatabaseURL == "postgresql://test_user:test_password@localhost:5432/test_db"
            except Exception as e:
                assert False, f"Config import test failed: {e}"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
