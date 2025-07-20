"""
Utility functions for generating unique identifiers across the application.
This module provides consistent ID generation to avoid code duplication.
"""

import string
import random
import uuid


def generate_alphanumeric_id(length: int = 8) -> str:
    """
    Generate a random alphanumeric ID with uppercase letters and digits.

    Args:
        length (int): The length of the ID to generate. Defaults to 8.

    Returns:
        str: A random alphanumeric string of the specified length.
    """
    characters = string.ascii_uppercase + string.digits
    return "".join(random.choices(characters, k=length))


def generate_game_code(length: int = 9) -> str:
    """Generate a random game code consisting of uppercase letters and digits."""
    return generate_alphanumeric_id(length)


def generate_session_code() -> str:
    """Generate a session code for game sessions."""
    return generate_alphanumeric_id(9)


def generate_player_id() -> str:
    """Generate a unique player ID."""
    return generate_alphanumeric_id(8)


def generate_question_id() -> str:
    """Generate a unique question ID."""
    return generate_alphanumeric_id(8)


def generate_response_id() -> str:
    """Generate a unique response ID."""
    return generate_alphanumeric_id(12)


def generate_score_id() -> str:
    """Generate a unique score ID."""
    return generate_alphanumeric_id(8)


def generate_assignment_id() -> str:
    """Generate a unique assignment ID."""
    return generate_alphanumeric_id(8)


def generate_uuid_based_id(length: int = 6) -> str:
    """
    Generate a UUID-based ID (useful for temporary sessions).

    Args:
        length (int): The length of the ID to extract from UUID. Defaults to 6.

    Returns:
        str: A UUID-based string of the specified length in uppercase.
    """
    return str(uuid.uuid4())[:length].upper()
