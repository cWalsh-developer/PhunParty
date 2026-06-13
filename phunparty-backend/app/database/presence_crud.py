from datetime import UTC, datetime, timedelta

from app.schemas.players_model import Players
from app.schemas.social_models import PlayerPresence
from sqlalchemy.orm import Session

ONLINE_WINDOW_SECONDS = 60


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def is_presence_current(presence: PlayerPresence | None) -> bool:
    if not presence or not presence.is_online:
        return False

    return presence.updated_at >= utc_now() - timedelta(seconds=ONLINE_WINDOW_SECONDS)


def set_player_online(db: Session, player_id: str) -> PlayerPresence:
    now = utc_now()
    presence = (
        db.query(PlayerPresence).filter(PlayerPresence.player_id == player_id).first()
    )

    if presence:
        presence.is_online = True
        presence.last_seen_at = now
        presence.updated_at = now
    else:
        presence = PlayerPresence(
            player_id=player_id,
            is_online=True,
            last_seen_at=now,
            updated_at=now,
        )
        db.add(presence)

    db.commit()
    return presence


def set_player_offline(db: Session, player_id: str) -> PlayerPresence:
    now = utc_now()
    presence = (
        db.query(PlayerPresence).filter(PlayerPresence.player_id == player_id).first()
    )

    if presence:
        presence.is_online = False
        presence.last_seen_at = now
        presence.updated_at = now
    else:
        presence = PlayerPresence(
            player_id=player_id,
            is_online=False,
            last_seen_at=now,
            updated_at=now,
        )
        db.add(presence)

    db.commit()
    return presence


def get_presence_map(
    db: Session, player_ids: list[str]
) -> dict[str, PlayerPresence]:
    if not player_ids:
        return {}

    rows = (
        db.query(PlayerPresence)
        .filter(PlayerPresence.player_id.in_(player_ids))
        .all()
    )
    return {row.player_id: row for row in rows}


def visible_presence_for_player(
    player: Players, presence: PlayerPresence | None
) -> tuple[bool, datetime | None]:
    if not player.show_online_status:
        return False, None

    if not presence:
        return False, None

    return is_presence_current(presence), presence.last_seen_at
