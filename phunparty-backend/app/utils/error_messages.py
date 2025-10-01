# Custom error messages for different scenarios
ERROR_MESSAGES = {
    # Authentication errors
    "PLAYER_NOT_FOUND": "We couldn't find an account with that email address.",
    "INVALID_PASSWORD": "The password you entered is incorrect.",
    "LOGIN_FAILED": "Login failed. Please check your credentials and try again.",
    # Player management errors
    "PLAYER_CREATE_FAILED": "Unable to create your account. Please try again.",
    "PLAYER_UPDATE_FAILED": "Failed to update your profile. Please try again.",
    "PLAYER_DELETE_FAILED": "Unable to delete your account at this time.",
    "EMAIL_ALREADY_EXISTS": "An account with this email already exists.",
    # Game session errors
    "GAME_NOT_FOUND": "This game no longer exists.",
    "SESSION_NOT_FOUND": "This game session is no longer available.",
    "SESSION_CREATE_FAILED": "Unable to create game session. Please try again.",
    "JOIN_GAME_FAILED": "Could not join the game. It may be full or no longer active.",
    "GAME_ALREADY_STARTED": "This game has already started.",
    # Questions and scoring
    "NO_QUESTIONS_FOUND": "No questions available for this game.",
    "QUESTION_NOT_FOUND": "This question is no longer available.",
    "NO_SCORES_AVAILABLE": "No scores available for this game session yet.",
    "SUBMIT_ANSWER_FAILED": "Unable to submit your answer. Please try again.",
    # Photo management
    "PHOTO_UPLOAD_FAILED": "Failed to upload your photo. Please try again.",
    "PHOTO_DELETE_FAILED": "Unable to delete your photo at this time.",
    "INVALID_FILE_TYPE": "Please upload a valid image file (JPG, PNG, GIF, or WebP).",
    "FILE_TOO_LARGE": "Your photo is too large. Please choose a file under 5MB.",
    "NO_PHOTO_TO_DELETE": "You don't have a profile photo to delete.",
    # Generic fallbacks
    "DATABASE_ERROR": "A database error occurred. Please try again.",
    "SERVER_ERROR": "Something went wrong on our end. Please try again later.",
    "VALIDATION_ERROR": "The information you provided is invalid.",
    "NETWORK_ERROR": "Network connection issue. Please check your connection and try again.",
}


def get_error_message(error_key: str, default: str = None) -> str:
    """
    Get a custom error message by key, with optional default fallback.

    Args:
        error_key: The key for the error message
        default: Default message if key not found

    Returns:
        The custom error message
    """
    return ERROR_MESSAGES.get(error_key, default or "An unexpected error occurred.")


def get_user_friendly_error(
    exception: Exception, fallback_key: str = "SERVER_ERROR"
) -> str:
    """
    Convert any exception to a user-friendly error message.

    Args:
        exception: The exception that occurred
        fallback_key: The error key to use as fallback

    Returns:
        User-friendly error message
    """
    # You can add specific exception type handling here
    if "duplicate key" in str(exception).lower():
        return get_error_message("EMAIL_ALREADY_EXISTS")
    elif "foreign key" in str(exception).lower():
        return get_error_message("VALIDATION_ERROR")
    elif "connection" in str(exception).lower():
        return get_error_message("DATABASE_ERROR")
    else:
        return get_error_message(fallback_key)
