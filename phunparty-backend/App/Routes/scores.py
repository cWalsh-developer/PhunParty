from app.database.dbCRUD import *
from app.dependencies import get_db
from app.models.scores_model import Scores
from typing import List
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session


router = APIRouter()
@router.get("/{session_code}", response_model=List[Scores], tags=["Scores"])
def get_scores_by_session(session_code: str, db: Session = Depends(get_db)):
    """
    Retrieve scores for a specific game session.
    """
    scores = db.query(Scores).filter(Scores.session_code == session_code).all()
    if not scores:
        raise HTTPException(status_code=404, detail="No scores found for this session")
    return scores
