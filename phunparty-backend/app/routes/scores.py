from app.database.dbCRUD import *
from app.dependencies import get_db
from app.models.scores_model import Scores
from app.models.response_models import ScoresResponseModel
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from typing import List


router = APIRouter()


@router.get(
    "/{session_code}", response_model=List[ScoresResponseModel], tags=["Scores"]
)
def get_scores_by_session_route(session_code: str, db: Session = Depends(get_db)):
    """
    Retrieve scores for a specific game session.
    """
    scores = get_scores_by_session(db, session_code)
    if not scores:
        raise HTTPException(status_code=404, detail="No scores found for this session")
    return scores
