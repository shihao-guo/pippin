"""
LiteLLM-based Chat Skill that:
 - fetches user-provided key from secret manager
 - passes api_key=... to litellm
 - does NOT set any environment variable
"""

import logging
from typing import Optional, Dict, Any

from litellm import completion
from framework.api_management import api_manager
from framework.main import DigitalBeing

logger = logging.getLogger(__name__)


class ChatSkill:
    """Skill for chat/completion using LiteLLM with a user-provided key, if any."""

    def __init__(self):
        """
        We'll use skill_name = "lite_llm" and required_api_keys = ["LITELLM"].
        That means the key is stored under "LITE_LLM_LITELLM_API_KEY".
        """
        self.skill_name = "lite_llm"
        self.required_api_keys = ["LITELLM"]
        api_manager.register_required_keys(self.skill_name, self.required_api_keys)

        self._initialized = False
        self.model_name: Optional[str] = None
        self._provided_api_key: Optional[str] = None

    async def initialize(self) -> bool:
        """
        1) Load skill config from being.configs["skills_config"]["lite_llm"]["model_name"].
        2) Retrieve the user-provided key from secret manager as "LITELLM".
        3) Store them into instance variables. 
        """
        try:
            # Load the config from the being
            being = DigitalBeing()
            being.initialize()
            skill_cfg = being.configs.get("skills_config", {}).get("lite_llm", {})

            # e.g. "openai/gpt-4", "anthropic/claude-2", etc.
            self.model_name = skill_cfg.get("model_name", "openai/gpt-4o")
            logger.info(f"LiteLLM skill using model = {self.model_name}")

            # Retrieve the user's key from secret manager
            api_key = await api_manager.get_api_key(self.skill_name, "LITELLM")
            if api_key:
                logger.info("Found a user-provided LiteLLM key.")
                self._provided_api_key = api_key
            else:
                logger.info("No LITELLM key found; user might be using no-auth or external provider.")

            self._initialized = True
            return True

        except Exception as e:
            logger.error(f"Failed to initialize LiteLLM skill: {e}", exc_info=True)
            self._initialized = False
            return False

    async def get_chat_completion(
        self,
        prompt: str,
        system_prompt: str = "You are a helpful AI assistant.",
        max_tokens: int = 150,
    ) -> Dict[str, Any]:
        """
        Use litellm.completion() with model=self.model_name, 
        and pass api_key=self._provided_api_key if we have it.
        """
        if not self._initialized:
            return {
                "success": False,
                "error": "LiteLLM skill not initialized",
                "data": None,
            }

        try:
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

            # Just pass the user-provided key, if any:
            response = completion(
                model=self.model_name,
                messages=messages,
                max_tokens=max_tokens,
                temperature=0.7,
                api_key=self._provided_api_key,  # <--- important
            )

            choices = response.get("choices", [])
            if not choices:
                return {
                    "success": False,
                    "error": "No choices returned from LiteLLM",
                    "data": None,
                }

            content = choices[0].get("message", {}).get("content", "")
            finish_reason = choices[0].get("finish_reason", "")
            used_model = response.get("model", self.model_name)

            return {
                "success": True,
                "data": {
                    "content": content,
                    "finish_reason": finish_reason,
                    "model": used_model,
                },
                "error": None,
            }

        except Exception as e:
            logger.error(f"Error in LiteLLM chat completion: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "data": None,
            }


# Global instance
chat_skill = ChatSkill()
