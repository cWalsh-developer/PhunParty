from app.database.dbCRUD import get_question_by_id
from app.dependencies import get_db
from app.models.questions_model import Questions
from typing import List
from fastapi import APIRouter, HTTPException, Depends
from app.models.questions import AnswerVerification
from app.models.session_question_assignment import SessionQuestionAssignment
from sqlalchemy.orm import Session

router = APIRouter()

@router.get("/{question_id}", tags=["Questions"])
def get_question_by_id_route(question_id: str, db: Session = Depends(get_db)):
    """
    Retrieve a question by its ID.
    """
    question = get_question_by_id(question_id, db)
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")
    return question