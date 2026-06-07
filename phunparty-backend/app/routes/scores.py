from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database.dbCRUD import *
from app.dependencies import get_current_player, get_db
from app.models.response_models import ScoresResponseModel
from app.schemas.players_model import Players
from app.security.ownership import assert_session_member_or_owner

router = APIRouter()


@router.get(
    "/{session_code}",
    response_model=List[ScoresResponseModel],
    tags=["Scores"],
)
def get_scores_by_session_route(
    session_code: str,
    current_player: Players = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    try:
        assert_session_member_or_owner(db, current_player, session_code)

        scores = get_scores_by_session(db, session_code)
        if not scores:
            raise HTTPException(
                status_code=404,
                detail="No scores available for this game session yet.",
            )

        return [
            {
                "display_name": score.player_display_name or "Player",
                "player_photo_url": score.player_photo_url,
                "score": score.score,
                "result": score.result,
                "session_code": score.session_code,
            }
            for score in scores
        ]

    except HTTPException:
        raise
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid session code provided.")
    except Exception:
        raise HTTPException(
            status_code=500,
            detail="Unable to retrieve scores at this time. Please try again.",
        )
