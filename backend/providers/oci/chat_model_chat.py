from __future__ import annotations

from typing import Any

import oci
from oci.generative_ai_inference import GenerativeAiInferenceClient
from oci.generative_ai_inference.models import (
    BaseChatRequest,
    ChatDetails,
    GenericChatRequest,
    Message,
    OnDemandServingMode,
    TextContent,
)

from backend.core.ports.chat_model import ChatModelPort


class OciChatModelChat(ChatModelPort):
    """OCI chat adapter using the Generative AI Inference Chat API."""

    def __init__(
        self,
        endpoint: str,
        compartment_id: str,
        model_id: str,
        auth_file_location: str = "~/.oci/config",
        auth_profile: str = "DEFAULT",
        **gen_kwargs: Any,
    ) -> None:
        if not model_id:
            raise ValueError("Chat adapter requires a model_id (model OCID)")
        if not model_id.startswith("ocid1."):
            raise ValueError("Chat adapter requires a model OCID")

        self._compartment_id = compartment_id
        self._model_id = model_id
        self._generation_params = {
            "max_tokens": gen_kwargs.get("max_tokens"),
            "temperature": gen_kwargs.get("temperature"),
            "top_p": gen_kwargs.get("top_p"),
            "top_k": gen_kwargs.get("top_k"),
            "frequency_penalty": gen_kwargs.get("frequency_penalty"),
            "presence_penalty": gen_kwargs.get("presence_penalty"),
        }

        try:
            config = oci.config.from_file(file_location=auth_file_location, profile_name=auth_profile)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("Failed to load OCI configuration for chat model") from exc

        try:
            self._client = GenerativeAiInferenceClient(
                config=config,
                service_endpoint=endpoint,
                retry_strategy=oci.retry.DEFAULT_RETRY_STRATEGY,
            )
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("Failed to initialize OCI Generative AI Inference client") from exc

    def generate(self, prompt: str) -> str:
        message = Message(
            role="USER",
            content=[TextContent(text=prompt)],
        )

        chat_request = GenericChatRequest(
            api_format=BaseChatRequest.API_FORMAT_GENERIC,
            messages=[message],
        )

        params = self._generation_params
        if params["max_tokens"] is not None:
            chat_request.max_tokens = params["max_tokens"]
        if params["temperature"] is not None:
            chat_request.temperature = params["temperature"]
        if params["top_p"] is not None:
            chat_request.top_p = params["top_p"]
        if params["top_k"] is not None:
            chat_request.top_k = params["top_k"]
        if params["frequency_penalty"] is not None:
            chat_request.frequency_penalty = params["frequency_penalty"]
        if params["presence_penalty"] is not None:
            chat_request.presence_penalty = params["presence_penalty"]

        details = ChatDetails(
            chat_request=chat_request,
            compartment_id=self._compartment_id,
            serving_mode=OnDemandServingMode(model_id=self._model_id),
        )

        try:
            response = self._client.chat(details)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("OCI chat generation request failed") from exc

        chat_response = getattr(response.data, "chat_response", None)
        if not chat_response:
            return ""

        choices = getattr(chat_response, "choices", None)
        if not choices:
            return ""

        message = getattr(choices[0], "message", None)
        if not message:
            return ""

        content = getattr(message, "content", None)
        if not content:
            return ""

        text = getattr(content[0], "text", "") if content else ""
        return (text or "").strip()
