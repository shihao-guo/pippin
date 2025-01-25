import logging
import aiohttp
import base64
from typing import List
from framework.composio_integration import composio_manager

logger = logging.getLogger(__name__)

async def upload_image_to_twitter(image_url: str) -> List[str]:
    """
    Downloads image from URL and uploads it to Twitter via Composio.
    Returns a list containing the media ID if successful, empty list otherwise.
    """
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(image_url) as response:
                if response.status != 200:
                    logger.warning(f"Failed to download image from {image_url}: {response.status}")
                    return []
                    
                image_data = await response.read()
                
            # Convert to base64
            base64_image = base64.b64encode(image_data).decode('utf-8')
            
            # Extract filename from URL or use default
            filename = image_url.split('/')[-1].split('?')[0] or 'image.png'
            
            # Upload to Twitter via Composio
            upload_response = composio_manager._toolset.execute_action(
                action="TWITTER_MEDIA_UPLOAD_MEDIA",
                params={
                    "media": {
                        "name": filename,
                        "content": base64_image
                    }
                },
                entity_id="MyDigitalBeing"
            )
            
            if upload_response.get("successful") or upload_response.get("successfull"):
                media_id = upload_response.get("media_id") or upload_response.get("data", {}).get("media_id")
                if media_id:
                    logger.info(f"Successfully uploaded image to Twitter, media_id: {media_id}")
                    return [media_id]
            
            logger.warning(f"Failed to upload image to Twitter: {upload_response.get('error', 'Unknown error')}")
            return []
                
    except Exception as e:
        logger.error(f"Error uploading image to Twitter: {e}", exc_info=True)
        return [] 