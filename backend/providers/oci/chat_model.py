from langchain_community.llms import OCIGenAI

from backend.core.ports.chat_model import ChatModelPort


class OciChatModel(ChatModelPort):
    def __init__(
        self,
        model_id: str,
        endpoint: str,
        compartment_id: str,
        auth_file_location: str | None = None,
        auth_profile: str | None = None,
        **gen_kwargs,
    ) -> None:
        if model_id.startswith("ocid1."):
            raise ValueError("OciChatModel supports alias IDs only; use OciChatModelChat for OCIDs")

        kwargs = {}
        if auth_file_location:
            kwargs["auth_file_location"] = auth_file_location
        if auth_profile:
            kwargs["auth_profile"] = auth_profile
        self._llm = OCIGenAI(
            model_id=model_id,
            service_endpoint=endpoint,
            compartment_id=compartment_id,
            **kwargs,
        )
        # Store generation kwargs to pass on each invocation (back-compat if empty)
        self._gen_kwargs = {
            k: v
            for k, v in {
                "max_tokens": gen_kwargs.get("max_tokens"),
                "temperature": gen_kwargs.get("temperature"),
                "top_p": gen_kwargs.get("top_p"),
                "top_k": gen_kwargs.get("top_k"),
                "frequency_penalty": gen_kwargs.get("frequency_penalty"),
                "presence_penalty": gen_kwargs.get("presence_penalty"),
            }.items()
            if v is not None
        }

    def generate(self, prompt: str) -> str:
        # Pass any configured generation kwargs to the underlying client
        return self._llm.invoke(prompt, **self._gen_kwargs).strip()
