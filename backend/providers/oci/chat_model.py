from langchain_community.llms import OCIGenAI

from core.ports.chat_model import ChatModelPort


class OciChatModel(ChatModelPort):
    def __init__(
        self,
        model_id: str,
        endpoint: str,
        compartment_id: str,
        auth_file_location: str | None = None,
        auth_profile: str | None = None,
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

    def generate(self, prompt: str) -> str:
        return self._llm.invoke(prompt).strip()
