from abc import ABC, abstractmethod

class ChatModelPort(ABC):
    @abstractmethod
    def generate(self, prompt: str) -> str: ...
