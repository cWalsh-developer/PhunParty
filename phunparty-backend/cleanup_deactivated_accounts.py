"""
Scheduled task to permanently delete deactivated accounts after grace period.
Run this script daily via cron job.
"""
import sys
import logging
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database.dbCRUD import cleanup_expired_deactivated_accounts
from app.config import settings

# Configure logging
import os
script_dir = os.path.dirname(os.path.abspath(__file__))
log_dir = os.path.join(script_dir, 'logs')
os.makedirs(log_dir, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(log_dir, 'account_cleanup.log')),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


def main():
    """Run the cleanup task"""
    try:
        logger.info("Starting deactivated account cleanup task...")
        
        # Create database session
        engine = create_engine(settings.DATABASE_URL)
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        db = SessionLocal()
        
        try:
            # Run cleanup
            result = cleanup_expired_deactivated_accounts(db)
            
            logger.info(
                f"Cleanup completed. Checked: {result['accounts_checked']}, "
                f"Deleted: {result['accounts_permanently_deleted']}, "
                f"Grace period: {result['grace_period_days']} days"
            )
            
            return 0
            
        except Exception as e:
            logger.error(f"Error during cleanup: {str(e)}", exc_info=True)
            return 1
            
        finally:
            db.close()
            
    except Exception as e:
        logger.error(f"Fatal error: {str(e)}", exc_info=True)
        return 1


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
