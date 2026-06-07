from app.database.dbCRUD import get_current_question_details
from app.dependencies import get_current_player, get_db
from app.logic.game_logic import (
    get_current_question_for_session,
    submit_player_answer,
    updateGameStartStatus,
)
from app.models.response_models import GameStatusResponse, SubmitAnswerRequest
from app.schemas.players_model import Players
from app.security.ownership import assert_session_member_or_owner, assert_session_owner
from app.security.rate_limit import enforce_rate_limit, get_client_ip
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

router = APIRouter()


def strip_answer_fields(value):
    if isinstance(value, dict):
        return {
            key: strip_answer_fields(item)
            for key, item in value.items()
            if key not in {"answer", "correct_index"}
        }
    if isinstance(value, list):
        return [strip_answer_fields(item) for item in value]
    return value


@router.post("/submit-answer", tags=["Game Logic"])
async def submit_answer(
    http_request: Request,
    request: SubmitAnswerRequest,
    current_player: Players = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """
    Submit a player's answer to the current question.
    Automatically advances game when all players have answered.
    """
    try:
        await enforce_rate_limit(
            http_request,
            scope="submit-answer-ip",
            identifier=get_client_ip(http_request),
            limit=120,
            window_seconds=60,
        )
        assert_session_member_or_owner(db, current_player, request.session_code)
        result = submit_player_answer(
            db=db,
            session_code=request.session_code,
            player_id=current_player.player_id,
            question_id=request.question_id,
            player_answer=request.player_answer,
        )
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail="Failed to submit answer")


@router.get(
    "/status/{session_code}", tags=["Game Logic"], response_model=GameStatusResponse
)
def get_session_status(
    session_code: str,
    current_player: Players = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """
    Get the current status of a game session including:
    - Current question
    - Player response counts
    - Game progression state
    """
    try:
        assert_session_member_or_owner(db, current_player, session_code)
        status = get_current_question_details(db, session_code)
        if "error" in status:
            raise HTTPException(status_code=404, detail=status["error"])
        return strip_answer_fields(status)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Unable to retrieve game status")


@router.get("/current-question/{session_code}", tags=["Game Logic"])
def get_current_question(
    session_code: str,
    current_player: Players = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """
    Get the current question for a game session
    """
    try:
        assert_session_member_or_owner(db, current_player, session_code)
        result = get_current_question_for_session(db, session_code)
        return strip_answer_fields(result)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail="Unable to retrieve current question"
        )


@router.put("/start-game/{session_code}", tags=["Game Logic"])
def start_game(
    session_code: str,
    current_player: Players = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """
    Start the game for a given session code.
    """
    try:
        assert_session_owner(db, current_player, session_code)
        updateGameStartStatus(db, session_code, True)
        return {"message": "Game started successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to start game")
