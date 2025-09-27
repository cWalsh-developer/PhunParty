from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database.dbCRUD import get_current_question_details
from app.dependencies import get_api_key, get_db
from app.logic.game_logic import (
    get_current_question_for_session,
    submit_player_answer,
    updateGameStartStatus,
)
from app.models.response_models import GameStatusResponse, SubmitAnswerRequest

router = APIRouter(dependencies=[Depends(get_api_key)])


@router.post("/submit-answer", tags=["Game Logic"])
def submit_answer(request: SubmitAnswerRequest, db: Session = Depends(get_db)):
    """
    Submit a player's answer to the current question.
    Automatically advances game when all players have answered.
    """
    try:
        result = submit_player_answer(
            db=db,
            session_code=request.session_code,
            player_id=request.player_id,
            question_id=request.question_id,
            player_answer=request.player_answer,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error submitting answer: {str(e)}"
        )


@router.get(
    "/status/{session_code}", tags=["Game Logic"], response_model=GameStatusResponse
)
def get_session_status(session_code: str, db: Session = Depends(get_db)):
    """
    Get the current status of a game session including:
    - Current question
    - Player response counts
    - Game progression state
    """
    try:
        status = get_current_question_details(db, session_code)
        if "error" in status:
            raise HTTPException(status_code=404, detail=status["error"])
        return status
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting status: {str(e)}")


@router.get("/current-question/{session_code}", tags=["Game Logic"])
def get_current_question(session_code: str, db: Session = Depends(get_db)):
    """
    Get the current question for a game session
    """
    try:
        result = get_current_question_for_session(db, session_code)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error getting current question: {str(e)}"
        )


@router.put("/start-game/{session_code}", tags=["Game Logic"])
def start_game(session_code: str, db: Session = Depends(get_db)):
    """
    Start the game for a given session code.
    """
    try:
        updateGameStartStatus(db, session_code, True)
        return {"message": "Game started successfully"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error getting current question: {str(e)}"
        )
