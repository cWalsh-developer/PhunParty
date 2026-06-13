from pydantic import BaseModel


class ProfileStatsResponse(BaseModel):
    player_id: str
    games_played: int
    wins: int
    losses: int
    draws: int
    win_percentage: float
    loss_percentage: float
    draw_percentage: float
