import logging
from typing import Dict, Any, List, Tuple

from framework.activity_decorator import activity, ActivityBase, ActivityResult
from framework.api_management import api_manager
from framework.memory import Memory
from skills.skill_chat import chat_skill
from skills.skill_generate_image import ImageGenerationSkill
from utils.twitter_utils import upload_image_to_twitter

logger = logging.getLogger(__name__)


@activity(
    name="post_a_tweet",
    energy_cost=0.4,
    cooldown=3600,  # 1 hour
    required_skills=["twitter_posting", "image_generation"],
)
class PostTweetActivity(ActivityBase):
    """
    Uses a chat skill (OpenAI) to generate tweet text,
    referencing the character's personality from character_config.
    Checks recent tweets in memory to avoid duplication.
    Posts to Twitter via Composio's "Creation of a post" dynamic action.
    """

    def __init__(self):
        super().__init__()
        self.max_length = 500
        # The Composio action name from your logs
        self.composio_action = "TWITTER_CREATION_OF_A_POST"
        # If you know your Twitter username, you can embed it in the link
        # or fetch it dynamically. Otherwise, substitute accordingly:
        self.twitter_username = "dittowalletbot"
        self.default_size = (1024, 1024)  # Added for image generation
        self.default_format = "png"  # Added for image generation

    async def execute(self, shared_data) -> ActivityResult:
        try:
            logger.info("Starting tweet posting activity...")

            # 1) Initialize the chat skill
            if not await chat_skill.initialize():
                return ActivityResult(
                    success=False, error="Failed to initialize chat skill"
                )

            # 2) Gather personality + recent tweets
            character_config = self._get_character_config(shared_data)
            personality_data = character_config.get("personality", {})
            recent_tweets = self._get_recent_tweets(shared_data, limit=10)

            # 3) Generate tweet text with chat skill
            prompt_text = self._build_chat_prompt(personality_data, recent_tweets)
            chat_response = await chat_skill.get_chat_completion(
                prompt=prompt_text,
                system_prompt="You are an AI that composes tweets with the given personality.",
                max_tokens=100,
            )
            if not chat_response["success"]:
                return ActivityResult(success=False, error=chat_response["error"])

            tweet_text = chat_response["data"]["content"].strip()
            if len(tweet_text) > self.max_length:
                tweet_text = tweet_text[: self.max_length - 3] + "..."

            # 4) Generate an image based on the tweet text
            image_prompt, media_ids = await self._generate_image_for_tweet(tweet_text, personality_data)

            # 5) Post the tweet via Composio (now with media)
            post_result = self._post_tweet_via_composio(tweet_text, media_ids)
            if not post_result["success"]:
                error_msg = post_result.get(
                    "error", "Unknown error posting tweet via Composio"
                )
                logger.error(f"Tweet posting failed: {error_msg}")
                return ActivityResult(success=False, error=error_msg)

            tweet_id = post_result.get("tweet_id")
            tweet_link = (
                f"https://twitter.com/{self.twitter_username}/status/{tweet_id}"
                if tweet_id
                else None
            )

            # 6) Return success, adding link & prompt in metadata
            logger.info(f"Successfully posted tweet: {tweet_text[:50]}...")
            return ActivityResult(
                success=True,
                data={"tweet_id": tweet_id, "content": tweet_text},
                metadata={
                    "length": len(tweet_text),
                    "method": "composio",
                    "model": chat_response["data"].get("model"),
                    "finish_reason": chat_response["data"].get("finish_reason"),
                    "tweet_link": tweet_link,
                    "chat_prompt_used": prompt_text,
                    "image_prompt_used": image_prompt,
                    "has_image": len(media_ids) > 0,
                },
            )

        except Exception as e:
            logger.error(f"Failed to post tweet: {e}", exc_info=True)
            return ActivityResult(success=False, error=str(e))

    def _get_character_config(self, shared_data) -> Dict[str, Any]:
        """
        Retrieve character_config from SharedData['system'] or re-init the Being if not found.
        """
        system_data = shared_data.get_category_data("system")
        maybe_config = system_data.get("character_config")
        if maybe_config:
            return maybe_config

        # fallback
        from framework.main import DigitalBeing

        being = DigitalBeing()
        being.initialize()
        return being.configs.get("character_config", {})

    def _get_recent_tweets(self, shared_data, limit: int = 2) -> List[str]:
        """
        Fetch the last N tweets posted (activity_type='PostTweetActivity') from memory.
        """
        system_data = shared_data.get_category_data("system")
        memory_obj: Memory = system_data.get("memory_ref")

        if not memory_obj:
            from framework.main import DigitalBeing

            being = DigitalBeing()
            being.initialize()
            memory_obj = being.memory

        recent_activities = memory_obj.get_recent_activities(limit=50, offset=0)
        tweets = []
        for act in recent_activities:
            if act.get("activity_type") == "PostTweetActivity" and act.get("success"):
                tweet_body = act.get("data", {}).get("content", "")
                if tweet_body:
                    tweets.append(tweet_body)

        return tweets[:limit]

    def _build_chat_prompt(
        self, personality: Dict[str, Any], recent_tweets: List[str]
    ) -> str:
        """
        Construct the user prompt referencing personality + last tweets.
        """
        trait_lines = [f"{t}: {v}" for t, v in personality.items()]
        personality_str = "\n".join(trait_lines)

        if recent_tweets:
            last_tweets_str = "\n".join(f"- {txt}" for txt in recent_tweets)
        else:
            last_tweets_str = "(No recent tweets)"

        return (
            f"Our digital being has these personality traits:\n"
            f"{personality_str}\n\n"
            f"Here are recent tweets:\n"
            f"{last_tweets_str}\n\n"
            f"Write a new short tweet (under 280 chars)"
            f"but not repeating old tweets. Avoid hashtags or repeated phrases.\n"
        )

    def _build_image_prompt(self, tweet_text: str, personality: Dict[str, Any]) -> str:
        personality_str = "\n".join(f"{t}: {v}" for t, v in personality.items())
        return f"Our digital being has these personality traits:\n" \
               f"{personality_str}\n\n" \
               f"And is creating a tweet with the text: {tweet_text}\n\n" \
               f"Generate a simple, whimsical and fun image that represents the story of the tweet and reflects the personality traits. Do not include the tweet text in the image."

    async def _generate_image_for_tweet(self, tweet_text: str, personality_data: Dict[str, Any]) -> Tuple[str, List[str]]:
        """
        Generate an image for the tweet and upload it to Twitter.
        Returns a tuple of (image_prompt, media_ids).
        If generation fails, returns (None, []).
        """
        logger.info("Decided to generate an image for tweet")
        image_skill = ImageGenerationSkill({
            "enabled": True,
            "max_generations_per_day": 50,
            "supported_formats": ["png", "jpg"],
        })

        if await image_skill.can_generate():
            image_prompt = self._build_image_prompt(tweet_text, personality_data)
            image_result = await image_skill.generate_image(
                prompt=image_prompt,
                size=self.default_size,
                format=self.default_format
            )
            
            if image_result.get("success") and image_result.get("image_data", {}).get("url"):
                media_ids = await upload_image_to_twitter(image_result["image_data"]["url"])
                return image_prompt, media_ids
        else:
            logger.warning("Image generation not available, proceeding with text-only tweet")
        
        return None, []

    def _post_tweet_via_composio(self, tweet_text: str, media_ids: List[str] = None) -> Dict[str, Any]:
        """
        Post a tweet using the "Creation of a post" Composio action.
        Now supports media uploads via media_ids parameter.
        """
        try:
            from framework.composio_integration import composio_manager

            logger.info(
                f"Posting tweet via Composio action='{self.composio_action}', text='{tweet_text[:50]}...', media_count={len(media_ids) if media_ids else 0}"
            )

            params = {"text": tweet_text}
            if media_ids:
                params["media__media__ids"] = media_ids

            response = composio_manager._toolset.execute_action(
                action=self.composio_action,
                params=params,
                entity_id="MyDigitalBeing",
            )

            # The actual success key is "successfull" (with 2 Ls)
            success_val = response.get("success", response.get("successfull"))
            if success_val:
                data_section = response.get("data", {})
                nested_data = data_section.get("data", {})
                tweet_id = nested_data.get("id")
                return {"success": True, "tweet_id": tweet_id}
            else:
                return {
                    "success": False,
                    "error": response.get("error", "Unknown or missing success key"),
                }

        except Exception as e:
            logger.error(f"Error in Composio tweet post: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
