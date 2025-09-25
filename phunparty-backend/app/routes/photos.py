import os
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.database.dbCRUD import get_player_by_ID, update_player
from app.dependencies import get_api_key, get_db

# Create photos directory if it doesn't exist
UPLOAD_DIR = Path("uploads/photos")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Allowed image formats
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB

# DiceBear avatar styles for generated avatars
DICEBEAR_STYLES = [
    "adventurer",
    "avataaars",
    "big-ears",
    "bottts",
    "identicon",
    "initials",
    "personas",
]

router = APIRouter(dependencies=[Depends(get_api_key)])


def is_valid_image(filename: str) -> bool:
    """Check if the file has a valid image extension."""
    return Path(filename).suffix.lower() in ALLOWED_EXTENSIONS


@router.post("/upload/{player_id}", tags=["Photos"])
async def upload_player_photo(
    player_id: str, file: UploadFile = File(...), db: Session = Depends(get_db)
):
    """
    Upload a profile photo for a player.
    """
    # Check if player exists
    player = get_player_by_ID(db, player_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    # Validate file
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    if not is_valid_image(file.filename):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file type. Allowed: {', '.join(ALLOWED_EXTENSIONS)}",
        )

    # Check file size
    file_content = await file.read()
    if len(file_content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="File too large. Max size: 5MB")

    # Generate unique filename
    file_extension = Path(file.filename).suffix.lower()
    unique_filename = f"{player_id}_{uuid.uuid4()}{file_extension}"
    file_path = UPLOAD_DIR / unique_filename

    try:
        # Save file
        with open(file_path, "wb") as buffer:
            buffer.write(file_content)

        # Update player record with photo URL
        photo_url = f"/photos/{unique_filename}"

        # Update player in database
        from app.database.dbCRUD import update_player_photo

        update_player_photo(db, player_id, photo_url)

        return {
            "message": "Photo uploaded successfully",
            "photo_url": photo_url,
            "filename": unique_filename,
        }

    except Exception as e:
        # Clean up file if database update fails
        if file_path.exists():
            file_path.unlink()
        raise HTTPException(status_code=500, detail=f"Failed to upload photo: {str(e)}")


@router.delete("/{player_id}/photo", tags=["Photos"])
async def delete_player_photo(player_id: str, db: Session = Depends(get_db)):
    """
    Delete a player's profile photo.
    """
    player = get_player_by_ID(db, player_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    if not player.profile_photo_url:
        raise HTTPException(status_code=404, detail="Player has no photo to delete")

    # Extract filename from URL
    filename = player.profile_photo_url.split("/")[-1]
    file_path = UPLOAD_DIR / filename

    try:
        # Delete file if it exists
        if file_path.exists():
            file_path.unlink()

        # Update database
        from app.database.dbCRUD import update_player_photo

        update_player_photo(db, player_id, None)

        return {"message": "Photo deleted successfully"}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete photo: {str(e)}")


@router.post("/avatar/{player_id}", tags=["Photos"])
async def set_player_avatar(
    player_id: str,
    avatar_style: str,
    avatar_seed: str = "default",
    db: Session = Depends(get_db),
):
    """
    Set a DiceBear generated avatar for a player.
    """
    # Check if player exists
    player = get_player_by_ID(db, player_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    # Validate avatar style
    if avatar_style not in DICEBEAR_STYLES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid avatar style. Available: {', '.join(DICEBEAR_STYLES)}",
        )

    try:
        # Generate DiceBear avatar URL
        avatar_url = (
            f"https://api.dicebear.com/7.x/{avatar_style}/png?seed={avatar_seed}"
        )

        # Update player in database
        from app.database.dbCRUD import update_player_photo

        update_player_photo(db, player_id, avatar_url)

        return {
            "message": "Avatar set successfully",
            "photo_url": avatar_url,
            "avatar_style": avatar_style,
            "avatar_seed": avatar_seed,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to set avatar: {str(e)}")


@router.get("/avatars", tags=["Photos"])
async def get_available_avatars():
    """
    Get list of available DiceBear generated avatars.
    """
    avatars = []
    for style in DICEBEAR_STYLES:
        # Generate 20 different avatars per style using numeric seeds
        for seed in range(1, 21):
            avatars.append(
                {
                    "name": f"{style}-{seed}",
                    "style": style,
                    "seed": str(seed),
                    "url": f"https://api.dicebear.com/7.x/{style}/png?seed={seed}",
                    "preview_url": f"https://api.dicebear.com/7.x/{style}/png?seed={seed}&size=64",  # Small preview
                }
            )

    return {"avatars": avatars, "total_count": len(avatars), "styles": DICEBEAR_STYLES}


@router.get("/avatars/styles", tags=["Photos"])
async def get_avatar_styles():
    """
    Get available avatar styles only.
    """
    return {
        "styles": [
            {
                "name": style,
                "example_url": f"https://api.dicebear.com/7.x/{style}/png?seed=example",
                "description": f"DiceBear {style} style avatars",
            }
            for style in DICEBEAR_STYLES
        ]
    }


@router.get("/avatars/generate/{style}", tags=["Photos"])
async def generate_avatar_preview(style: str, seed: str = "preview", size: int = 128):
    """
    Generate a preview of an avatar with specific style and seed.
    """
    if style not in DICEBEAR_STYLES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid avatar style. Available: {', '.join(DICEBEAR_STYLES)}",
        )

    # Validate size (DiceBear supports sizes up to 512)
    if size < 16 or size > 512:
        size = 128

    avatar_url = f"https://api.dicebear.com/7.x/{style}/png?seed={seed}&size={size}"

    return {"style": style, "seed": seed, "size": size, "url": avatar_url}


@router.get("/{filename}", tags=["Photos"])
async def get_photo(filename: str):
    """
    Serve a photo file.
    """
    file_path = UPLOAD_DIR / filename

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Photo not found")

    # You might want to use FileResponse here for better performance
    from fastapi.responses import FileResponse

    return FileResponse(
        path=file_path,
        media_type="image/*",
        headers={"Cache-Control": "max-age=3600"},  # Cache for 1 hour
    )
