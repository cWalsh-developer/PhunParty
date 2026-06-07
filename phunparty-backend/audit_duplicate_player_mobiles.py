from sqlalchemy import text

from app.config import SessionLocal


NORMALIZED_MOBILE_SQL = """
CASE
    WHEN player_mobile LIKE '0%'
    THEN '+44' || substring(player_mobile from 2)
    ELSE player_mobile
END
"""


def main() -> None:
    with SessionLocal() as db:
        duplicate_groups = db.execute(
            text(
                f"""
                SELECT {NORMALIZED_MOBILE_SQL} AS normalized_mobile,
                       COUNT(*) AS account_count
                FROM players
                WHERE player_mobile IS NOT NULL
                AND is_deleted = FALSE
                GROUP BY normalized_mobile
                HAVING COUNT(*) > 1
                ORDER BY account_count DESC, normalized_mobile
                """
            )
        ).mappings().all()

        if not duplicate_groups:
            print("No duplicate non-deleted player_mobile values found.")
            return

        print("Duplicate non-deleted player_mobile groups:")
        for group in duplicate_groups:
            normalized_mobile = group["normalized_mobile"]
            print(f"\n{normalized_mobile} ({group['account_count']} accounts)")

            rows = db.execute(
                text(
                    f"""
                    SELECT player_id,
                           player_name,
                           player_email,
                           player_mobile,
                           is_deactivated,
                           is_deleted,
                           deactivated_at,
                           deleted_at
                    FROM players
                    WHERE player_mobile IS NOT NULL
                    AND is_deleted = FALSE
                    AND {NORMALIZED_MOBILE_SQL} = :normalized_mobile
                    ORDER BY is_deactivated ASC, player_email NULLS LAST, player_id
                    """
                ),
                {"normalized_mobile": normalized_mobile},
            ).mappings().all()

            for row in rows:
                print(
                    "  - "
                    f"player_id={row['player_id']} "
                    f"name={row['player_name']!r} "
                    f"email={row['player_email']!r} "
                    f"mobile={row['player_mobile']!r} "
                    f"is_deactivated={row['is_deactivated']} "
                    f"deactivated_at={row['deactivated_at']!r}"
                )

        print(
            "\nResolve each group by keeping one non-deleted account per phone number. "
            "For duplicates you do not want to keep, set player_mobile to NULL, "
            "set it to a different verified phone number, or permanently anonymize/delete "
            "the account so its mobile is cleared."
        )


if __name__ == "__main__":
    main()
