import glob
import os
from urllib.parse import quote
import uuid
from io import BytesIO
from pathlib import Path

from app.database.dbCRUD import get_player_by_ID, update_player_photo
from app.dependencies import get_current_player, get_db, require_admin_api_key
from app.schemas.players_model import Players
from app.security.cache import cache, invalidate_profile_cache
from app.security.input_validation import validate_avatar_seed
from app.security.ownership import assert_same_player
from app.security.rate_limit import enforce_rate_limit, get_client_ip
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from PIL import Image, ImageOps
from sqlalchemy.orm import Session

# Create photos directory if it doesn't exist
UPLOAD_DIR = Path("uploads/photos")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Allowed image formats
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_IMAGE_FORMATS = {"JPEG", "PNG", "WEBP"}
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

router = APIRouter()


def cleanup_old_player_photos(player_id: str, upload_dir: Path = UPLOAD_DIR):
    """
    Delete all existing uploaded photos for a specific player before uploading a new one.
    This prevents accumulation of old photos and saves storage space.
    """
    try:
        # Find all uploaded photos for this player (pattern: playerid_*.*)
        player_photos_pattern = f"{player_id}_*.*"
        player_photos = glob.glob(str(upload_dir / player_photos_pattern))

        deleted_count = 0
        for photo_path in player_photos:
            try:
                os.remove(photo_path)
                deleted_count += 1
                print(f"🗑️ Deleted old photo: {os.path.basename(photo_path)}")
            except OSError as e:
                print(f"⚠️ Could not delete {photo_path}: {e}")

        if deleted_count > 0:
            print(f"✅ Cleaned up {deleted_count} old photos for player {player_id}")

    except Exception as e:
        print(f"❌ Error during photo cleanup for player {player_id}: {e}")
        # Don't raise exception - continue with upload even if cleanup fails


def cleanup_old_player_photos_safe(
    player_id: str, current_photo_url: str, upload_dir: Path = UPLOAD_DIR
):
    """
    Delete old photos for a player, but keep the current one from database.
    More conservative approach that preserves the currently stored photo.
    """
    try:
        # Extract filename from current photo URL if it exists
        current_filename = None
        if current_photo_url and "/photos/" in current_photo_url:
            current_filename = current_photo_url.split("/")[-1]

        # Find all uploaded photos for this player
        player_photos_pattern = f"{player_id}_*.*"
        player_photos = glob.glob(str(upload_dir / player_photos_pattern))

        deleted_count = 0
        for photo_path in player_photos:
            photo_filename = os.path.basename(photo_path)

            # Skip if this is the current photo in database
            if current_filename and photo_filename == current_filename:
                continue

            try:
                os.remove(photo_path)
                deleted_count += 1
                print(f"🗑️ Deleted old photo: {photo_filename}")
            except OSError as e:
                print(f"⚠️ Could not delete {photo_path}: {e}")

        if deleted_count > 0:
            print(
                f"✅ Safely cleaned up {deleted_count} old photos for player {player_id}"
            )

    except Exception as e:
        print(f"❌ Error during safe photo cleanup for player {player_id}: {e}")


def validate_uploaded_image(filename: str, content: bytes) -> str:
    if not filename:
        raise HTTPException(status_code=400, detail="No file provided")

    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Invalid image extension")

    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="File too large. Max size: 5MB")

    try:
        image = Image.open(BytesIO(content))
        image.verify()
    except Exception:
        raise HTTPException(
            status_code=400, detail="Uploaded file is not a valid image"
        )

    image = Image.open(BytesIO(content))
    if image.format not in ALLOWED_IMAGE_FORMATS:
        raise HTTPException(status_code=400, detail="Unsupported image format")

    return suffix


def strip_image_metadata(content: bytes, suffix: str) -> bytes:
    try:
        image = Image.open(BytesIO(content))
        image = ImageOps.exif_transpose(image)

        output = BytesIO()
        if suffix in {".jpg", ".jpeg"}:
            if image.mode not in {"RGB", "L"}:
                image = image.convert("RGB")
            image.save(output, format="JPEG", quality=90, optimize=True)
        elif suffix == ".png":
            image.save(output, format="PNG", optimize=True)
        elif suffix == ".webp":
            image.save(output, format="WEBP", quality=90, method=6)
        else:
            raise HTTPException(status_code=400, detail="Unsupported image format")

        return output.getvalue()
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="Could not process image")


def safe_photo_path(filename: str) -> Path:
    safe_name = Path(filename).name
    if safe_name != filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    file_path = UPLOAD_DIR / safe_name
    resolved_upload_dir = UPLOAD_DIR.resolve()
    resolved_file_path = file_path.resolve()

    if not str(resolved_file_path).startswith(str(resolved_upload_dir)):
        raise HTTPException(status_code=400, detail="Invalid filename")

    return file_path


@router.post("/upload/{player_id}", tags=["Photos"])
async def upload_player_photo(
    player_id: str,
    request: Request,
    file: UploadFile = File(...),
    current_player: Players = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """
    Upload a profile photo for a player.
    """
    assert_same_player(current_player, player_id)
    await enforce_rate_limit(
        request,
        scope="photos-upload-player",
        identifier=current_player.player_id,
        limit=10,
        window_seconds=3600,
    )

    try:
        # Check if player exists
        player = get_player_by_ID(db, player_id)
        if not player:
            raise HTTPException(status_code=404, detail="Player not found")

        file_content = await file.read()
        file_extension = validate_uploaded_image(file.filename, file_content)
        sanitized_content = strip_image_metadata(file_content, file_extension)

        cleanup_old_player_photos(player_id, UPLOAD_DIR)

        # Generate unique filename
        unique_filename = f"{player_id}_{uuid.uuid4()}{file_extension}"
        file_path = UPLOAD_DIR / unique_filename

        # Save file
        with open(file_path, "wb") as buffer:
            buffer.write(sanitized_content)

        # Update player record with photo URL
        photo_url = f"/photos/{unique_filename}"

        # Update player in database
        update_player_photo(db, player_id, photo_url)
        invalidate_profile_cache(player_id)

        return {
            "message": "Photo uploaded successfully",
            "photo_url": photo_url,
            "filename": unique_filename,
        }
    except HTTPException:
        raise
    except Exception as e:
        # Clean up file if database update fails
        if "file_path" in locals() and file_path.exists():
            file_path.unlink()
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{player_id}/photo", tags=["Photos"])
async def delete_player_photo(
    player_id: str,
    current_player: Players = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """
    Delete a player's profile photo.
    """
    assert_same_player(current_player, player_id)

    try:
        player = get_player_by_ID(db, player_id)
        if not player:
            raise HTTPException(status_code=404, detail="Player not found")

        if not player.profile_photo_url:
            raise HTTPException(status_code=404, detail="Player has no photo to delete")

        # Extract filename from URL
        filename = player.profile_photo_url.split("/")[-1]
        file_path = safe_photo_path(filename)

        # Delete file if it exists
        if file_path.exists():
            file_path.unlink()

        # Update database
        update_player_photo(db, player_id, None)
        invalidate_profile_cache(player_id)

        return {"message": "Photo deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/avatar/{player_id}", tags=["Photos"])
async def set_player_avatar(
    player_id: str,
    request: Request,
    avatar_style: str,
    avatar_seed: str = "default",
    current_player: Players = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """
    Set a DiceBear generated avatar for a player.
    """
    assert_same_player(current_player, player_id)
    await enforce_rate_limit(
        request,
        scope="photos-avatar-player",
        identifier=current_player.player_id,
        limit=30,
        window_seconds=3600,
    )

    try:
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

        # Clean up old uploaded photos since we're switching to avatar
        # Avatars are external URLs, so we can safely delete all uploaded files
        cleanup_old_player_photos(player_id, UPLOAD_DIR)

        avatar_seed = validate_avatar_seed(avatar_seed)
        encoded_seed = quote(avatar_seed, safe="")

        # Generate DiceBear avatar URL
        avatar_url = (
            f"https://api.dicebear.com/7.x/{avatar_style}/png?seed={encoded_seed}"
        )

        # Update player in database
        update_player_photo(db, player_id, avatar_url)
        invalidate_profile_cache(player_id)

        return {
            "message": "Avatar set successfully",
            "photo_url": avatar_url,
            "avatar_style": avatar_style,
            "avatar_seed": avatar_seed,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/avatars", tags=["Photos"])
async def get_available_avatars():
    """
    Get list of available DiceBear generated avatars.
    """
    try:
        cache_key = "photos:avatars"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

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

        response = {
            "avatars": avatars,
            "total_count": len(avatars),
            "styles": DICEBEAR_STYLES,
        }
        cache.set(cache_key, response, ttl_seconds=86400)
        return response
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/avatars/styles", tags=["Photos"])
async def get_avatar_styles():
    """
    Get available avatar styles only.
    """
    try:
        cache_key = "photos:avatar_styles"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        response = {
            "styles": [
                {
                    "name": style,
                    "example_url": f"https://api.dicebear.com/7.x/{style}/png?seed=example",
                    "description": f"DiceBear {style} style avatars",
                }
                for style in DICEBEAR_STYLES
            ]
        }
        cache.set(cache_key, response, ttl_seconds=86400)
        return response
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/avatars/generate/{style}", tags=["Photos"])
async def generate_avatar_preview(
    style: str,
    request: Request,
    seed: str = "preview",
    size: int = 128,
):
    """
    Generate a preview of an avatar with specific style and seed.
    """
    try:
        await enforce_rate_limit(
            request,
            scope="photos-avatar-generate-ip",
            identifier=get_client_ip(request),
            limit=120,
            window_seconds=3600,
        )
        if style not in DICEBEAR_STYLES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid avatar style. Available: {', '.join(DICEBEAR_STYLES)}",
            )

        # Validate size (DiceBear supports sizes up to 512)
        if size < 16 or size > 512:
            size = 128

        seed = validate_avatar_seed(seed)
        encoded_seed = quote(seed, safe="")
        avatar_url = (
            f"https://api.dicebear.com/7.x/{style}/png?seed={encoded_seed}&size={size}"
        )

        return {"style": style, "seed": seed, "size": size, "url": avatar_url}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{filename}", tags=["Photos"])
async def get_photo(filename: str):
    """
    Serve a photo file.
    """
    try:
        file_path = safe_photo_path(filename)

        if not file_path.exists():
            raise HTTPException(status_code=404, detail="Photo not found")

        return FileResponse(
            path=file_path,
            media_type="image/*",
            headers={"Cache-Control": "public, max-age=86400, immutable"},
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/maintenance/cleanup-orphaned", tags=["Photos"])
async def cleanup_orphaned_photos(
    db: Session = Depends(get_db),
    _: str = Depends(require_admin_api_key),
):
    """
    Maintenance endpoint to clean up orphaned photos (files that exist but aren't referenced in database).
    Use with caution - only run this occasionally for maintenance.
    """
    try:
        from app.database.dbCRUD import get_all_players

        # Get all uploaded photo files
        all_photo_files = glob.glob(str(UPLOAD_DIR / "*_*.*"))

        # Get all players with photos from database
        players = get_all_players(db)  # You'll need to implement this in dbCRUD
        referenced_files = set()

        for player in players:
            if player.profile_photo_url and "/photos/" in player.profile_photo_url:
                filename = player.profile_photo_url.split("/")[-1]
                referenced_files.add(str(UPLOAD_DIR / filename))

        # Find orphaned files
        orphaned_files = [f for f in all_photo_files if f not in referenced_files]

        deleted_count = 0
        for orphaned_file in orphaned_files:
            try:
                os.remove(orphaned_file)
                deleted_count += 1
                print(f"🗑️ Deleted orphaned photo: {os.path.basename(orphaned_file)}")
            except OSError as e:
                print(f"⚠️ Could not delete {orphaned_file}: {e}")

        return {
            "message": "Orphaned photo cleanup completed",
            "total_files_found": len(all_photo_files),
            "referenced_files": len(referenced_files),
            "orphaned_files_deleted": deleted_count,
            "storage_saved": f"~{deleted_count * 0.5}MB (estimated)",
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/maintenance/storage-info", tags=["Photos"])
async def get_storage_info(_: str = Depends(require_admin_api_key)):
    """
    Get information about photo storage usage.
    """
    try:
        all_files = glob.glob(str(UPLOAD_DIR / "*.*"))
        total_size = sum(os.path.getsize(f) for f in all_files)

        return {
            "upload_directory": str(UPLOAD_DIR),
            "total_files": len(all_files),
            "total_size_bytes": total_size,
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "average_file_size_kb": (
                round((total_size / len(all_files)) / 1024, 2) if all_files else 0
            ),
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
