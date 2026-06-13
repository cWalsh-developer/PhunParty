from app.models.enums import ResultType
from app.schemas.scores_model import Scores
from sqlalchemy.orm import Session


def get_player_stats_summary(db: Session, player_id: str) -> dict:
    scores = (
        db.query(Scores)
        .filter(Scores.player_id == player_id)
        .filter(Scores.result.isnot(None))
        .all()
    )

    games_played = len(scores)
    wins = sum(1 for score in scores if score.result == ResultType.win)
    losses = sum(1 for score in scores if score.result == ResultType.lose)
    draws = sum(1 for score in scores if score.result == ResultType.draw)

    def percentage(count: int) -> float:
        if games_played == 0:
            return 0.0
        return round((count / games_played) * 100, 1)

    return {
        "player_id": player_id,
        "games_played": games_played,
        "wins": wins,
        "losses": losses,
        "draws": draws,
        "win_percentage": percentage(wins),
        "loss_percentage": percentage(losses),
        "draw_percentage": percentage(draws),
    }
